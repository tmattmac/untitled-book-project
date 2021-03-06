from sqlalchemy import or_, and_, desc, func, nullslast, text
from models import *
from lib.epubtag import EpubBook
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

SORT_FIELDS = {
        'title': UserBook.title,
        'publisher': UserBook.publisher,
        'publication_year': UserBook.publication_year,
        'last_read': UserBook.last_read,
        'author': Author.name
    }

DEFAULT_PER_PAGE = 50

def build_query(user, **kwargs):
    """
    Build a query on books based on provided query parameters
    q       search query
    pg      page of results (default: 1)
    per_pg  number of results to show per page (default: 20)
    author  filter by author with specified id
    sort    name of column to sort by (default: last_read)
    order   asc or desc (default: )
    """

    # TODO: Get items per page from user preferences
    pg = kwargs.get('pg', 1)
    per_pg = kwargs.get('per_pg', DEFAULT_PER_PAGE)
    sort = kwargs.get('sort')
    order = kwargs.get('order')
    author = kwargs.get('author')
    tag = kwargs.get('tag')
    q = kwargs.get('q')
    publisher = kwargs.get('publisher')
    year = kwargs.get('year')

    search_meta = {}

    query = UserBook.query\
        .filter(UserBook.user_id == user.id)

    if author:
        # TODO: Try-except block
        query = query.filter(UserBook.authors.any(Author.id.in_(author)))
        if len(author) == 1:
            author_instance = Author.query.get(author[0])
            if author_instance and author_instance.user_id == user.id:
                search_meta['author'] = author_instance.name
        else:
            search_meta['author'] = '(multiple authors)'
            
    if tag:
        query = query.filter(UserBook.tags.any(Tag.id.in_(tag)))
        if len(tag) == 1:
            tag_instance = Tag.query.get(tag[0])
            if tag_instance and tag_instance.user_id == user.id:
                search_meta['tag'] = tag_instance.tag_name
        else:
            search_meta['tag'] = '(multiple tags)'

    if publisher:
        search_meta['publisher'] = publisher
        query = query.filter(UserBook.publisher.ilike(f'%{publisher}%'))

    if year:
        search_meta['year'] = year
        query = query.filter(UserBook.publication_year == year)

    if q:
        search_meta['q'] = q
        q = f'%{q}%'
        query = query.filter(or_(
            UserBook.title.ilike(q),
            UserBook.publisher.ilike(q),
            UserBook.authors.any(Author.name.ilike(q)),
            UserBook.tags.any(Tag.tag_name.ilike(q))
        ))

    if not sort or sort not in SORT_FIELDS:
        sort = 'last_read'
        order = 'desc'
    elif sort == 'author':
        sort = Author.name

    if sort == 'last_read':
        order = order or 'desc'

    sort = SORT_FIELDS[sort]

    if order == 'desc':
        query = query.order_by(desc(sort).nullslast())
    else:
        query = query.order_by(nullslast(sort))

    # query = query\
    #         .limit(per_pg)\
    #         .offset(pg * per_pg)

    return query.paginate(pg, per_pg, error_out=False), search_meta

# TODO: After implementing association proxy, use populate_obj instead
def update_book_with_form_data(book_instance, form):
    book = {
        'title':            form.title.data,
        'publisher':        form.publisher.data,
        'publication_year': form.publication_year.data,
        'comments':         form.comments.data,
        'cover_image':      form.cover_image.data,
        'authors':          [],
        'tags':             []
    }
    for author_name in form.authors.data:
        author = get_or_create(Author, name=author_name, user_id=book_instance.user_id)
        book['authors'].append(author)
    for tag_name in form.tags.data:
        tag = get_or_create(Tag, tag_name=tag_name, user_id=book_instance.user_id)
        book['tags'].append(tag)

    for k, v in book.items():
        setattr(book_instance, k, v)

    db.session.add(book_instance)
    db.session.commit()


def parse_year(date_string):
    '''Pull the year out of a formatted date string'''
    try:
        return [part for part in date_string.split('-') if len(part) == 4][0]
    except:
        return None


def remove_page_curl(url):
    '''Remove edge=curl parameter from Google Books API cover image'''
    parsed_url = list(urlparse(url))
    query = parse_qs(parsed_url[4])
    if 'edge' in query:
        query.pop('edge')
    parsed_url[4] = urlencode(query, doseq=True)
    new_url = urlunparse(parsed_url)
    return new_url

def book_model_from_api_data(user_id, api_data):

    metadata = api_data['volumeInfo']

    # Get thumbnails if they exist, remove page curl from resulting url
    thumbnails = metadata.get('imageLinks')
    cover_image = thumbnails.get('thumbnail') if thumbnails else None
    cover_image = remove_page_curl(cover_image) if cover_image else None

    book = UserBook(
        user_id=user_id,
        gbooks_id=api_data['id'],
        title=metadata.get('title'),
        publisher=metadata.get('publisher'),
        publication_year=parse_year(metadata.get('publishedDate')),
        cover_image=cover_image
    )

    # Create list of authors and add to created book instance
    author_names = metadata.get('authors', [])
    book.authors = authors_from_author_list(author_names, user_id)

    return book

def authors_from_author_list(author_names, user_id):
    # Create any authors not already in database
    authors = Author.query.filter(
        and_(
            Author.name.in_(author_names),
            Author.user_id == user_id
        )
    ).all()
    author_names = set(author_names) - set([author.name for author in authors])
    for name in author_names:
        authors.append(Author(name=name, user_id=user_id))

    return authors

def extract_metadata_from_epub(file_handle):
    metadata = {}
    epub = EpubBook(file_handle)
    metadata['title'] = epub.get_title()
    metadata['authors'] = epub.get_authors()
    publisher, _, _ = epub.get_matches('dc:publisher')
    date, _, _ = epub.get_matches('dc:date')

    if publisher:
        metadata['publisher'] = publisher[0]
    if date:
        metadata['publication_year'] = parse_year(date[0])

    epub.close()
    
    return metadata

def get_tags(user_id):
    tags = db.session.query(Tag)\
        .filter(Tag.user_id==user_id)\
        .join(Tag.books)\
        .order_by(Tag.tag_name)\
        .all()

    return tags

def get_authors(user_id):
    authors = db.session.query(Author)\
        .filter(Author.user_id==user_id)\
        .join(Author.books)\
        .order_by(Author.name)\
        .all()

    return authors