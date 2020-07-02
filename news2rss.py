#!/usr/bin/env python3
#
# news2rss.py by lenormf
# A lightweight HTTP server that turns NewsAPI data into RSS feeds
#

import os
import sys
import logging
import inspect
import argparse

import pycountry

import bottle
from bottle import get, abort

from newsapi import NewsApiClient
from newsapi.newsapi_exception import NewsAPIException

from feedgen.feed import FeedGenerator


class NewsAPIPlugin(object):
    name = "news_api"
    api = 2

    def __init__(self, keyword="newsapi", api_key=None):
        self.keyword = keyword
        self.newsapi = NewsApiClient(api_key=api_key)

        try:
            self.newsapi._sources_cache = self.newsapi.get_sources()["sources"]
            logging.debug("sources cache: %r", self.newsapi._sources_cache)
        except NewsAPIException as e:
            logging.error("unable to fetch list of sources: %s", e)
            raise bottle.PluginError("unable to initialise NewsAPIPlugin")

    def setup(self, app):
        for other in app.plugins:
            if not isinstance(other, NewsAPIPlugin):
                continue

            if other.keyword == self.keyword:
                raise bottle.PluginError("Found another '%s' plugin with conflicting settings (non-unique keyword)." % self.name)

    def apply(self, callback, context):
        conf = context.config.get(NewsAPIPlugin.name) or {}
        keyword = conf.get("keyword", self.keyword)
        newsapi = conf.get("newsapi", self.newsapi)

        if self.keyword not in inspect.signature(callback).parameters:
            return callback

        def wrapper(*args, **kwargs):
            kwargs[keyword] = newsapi
            return callback(*args, **kwargs)

        return wrapper


def _feed_rss(sources_cache, query, query_meta, articles):
    feed = FeedGenerator()

    sources = []
    for article in articles:
        entry = feed.add_entry(order="append")

        logging.debug("article: %r", article)

        if "title" not in article:
            logging.error("no 'title' entry in the article")
            abort(401, "an error occurred while adding an entry")
        entry.title(article["title"])

        if "content" not in article:
            logging.error("no 'content' entry in the article")
            abort(401, "an error occurred while adding an entry")
        entry.content(article["content"])

        if "description" not in article:
            logging.error("no 'description' entry in the article")
            abort(401, "an error occurred while adding an entry")
        entry.description(article["description"])

        if "author" in article and article["author"]:
            entry.author(name=article["author"], email="e@ma.il")

        if "url" in article and article["url"]:
            entry.link(href=article["url"], rel="alternate")

        if "publishedAt" in article:
            entry.pubDate(article["publishedAt"])

        if article["source"] not in sources:
            sources.append(article["source"])

    def set_feed_meta(feed, title, link, description, **optional_fields):
        feed.title(title)
        feed.link(href=link, rel="self")
        feed.description(description)

        if "id" in optional_fields:
            feed.id(optional_fields["id"])

        if "category" in optional_fields:
            feed.category(term=optional_fields["category"])

        if "language" in optional_fields:
            feed.language(optional_fields["language"])

    if not sources:
        optional_fields = {}

        if "category" in query:
            optional_fields["category"] = query["category"]

        if "language" in query:
            optional_fields["language"] = query["language"]

        set_feed_meta(feed,
                      query_meta["description"], query_meta["url"],
                      query_meta["description"],
                      **optional_fields)
    elif len(sources) == 1:
        source = sources[0]
        source_meta = next((s for s in sources_cache
                            if source["id"] == s["id"]),
                           None)
        if source_meta:
            title, link, description = [None] * 3
            optional_fields = {}

            if "name" not in source_meta:
                logging.error("no 'name' entry in the source meta")
                abort(401, "an error occurred while generating the feed")
            title = source_meta["name"]

            if "url" not in source_meta:
                logging.error("no 'url' entry in the source meta")
                abort(401, "an error occurred while generating the feed")
            link = source_meta["url"]

            if "description" not in source_meta:
                logging.error("no 'description' entry in the source meta")
                abort(401, "an error occurred while generating the feed")
            description = source_meta["description"]

            if "id" in source_meta:
                optional_fields["id"] = source_meta["id"]

            if "category" in source_meta:
                optional_fields["category"] = source_meta["category"]

            if "language" in source_meta:
                optional_fields["language"] = source_meta["language"]

            set_feed_meta(feed, title, link, description, **optional_fields)
        else:
            set_feed_meta(feed,
                          query_meta["description"], query_meta["url"],
                          "News articles from %s" % (source["name"] or source["id"]))
    else:
        optional_fields = {}

        if "category" in query:
            optional_fields["category"] = query["category"]

        if "language" in query:
            optional_fields["language"] = query["language"]

        sources_names = []
        for source in sources:
            if source["name"]:
                sources_names.append(source["name"])
            elif source["id"]:
                if source["id"] in sources_cache:
                    sources_names.append(sources_cache[source["id"]]["name"] or source["id"])
                else:
                    sources_names.append(source["id"])

        set_feed_meta(feed,
                      query_meta["description"], query_meta["url"],
                      "News articles from %s" % ", ".join(sources_names),
                      **optional_fields)

    try:
        return feed.rss_str()
    except ValueError as e:
        logging.error("unable to generate feed: %s", e)
        abort(401, "an error occurred while generating the feed")


@get("/<feed_type>/<subset>/<query_path:path>")
def get_feed_sources(feed_type, subset, query_path, newsapi):
    feed_types = {
        "rss": _feed_rss,
    }
    newsapi_getters = {
        "all": newsapi.get_everything,
        "top": newsapi.get_top_headlines,
    }

    logging.debug("feed_type: %r", feed_type)
    logging.debug("subset: %r", subset)
    logging.debug("query_path: %r", query_path)

    if feed_type not in feed_types.keys():
        abort(401, "invalid feed type, must be one of: %s" % feed_types.keys())
    elif subset not in newsapi_getters.keys():
        abort(401, "invalid subset, must be one of: %s" % newsapi_getters.keys())

    # Turn the list into a dictionary (even items are keys, odd items are values)
    it = iter(query_path.split('/'))
    query = dict(zip(it, it))

    def get_query_description(query):
        tokens = []

        if "sources" in query:
            sources_names = []
            for s in query["sources"].split(','):
                if s in newsapi._sources_cache:
                    sources_names.append(newsapi._sources_cache[s]["name"] or s)
                else:
                    sources_names.append(s)

            tokens.append("from %s" % (", ".join(sources_names)))
        else:
            if "country" in query:
                country = pycountry.countries.get(alpha_2=query["country"].upper())
                if country:
                    country = country.name
                else:
                    country = query["country"]

                tokens.append("from country '%s'" % country)

            if "category" in query:
                tokens.append("in category '%s'" % query["category"])

        if "q" in query:
            tokens.append("that match '%s'" % query["q"])

        return "News articles %s" % ", ".join(tokens)

    # Generate human-readable information about the query
    query_meta = {
        "url": bottle.request.url,
        "description": get_query_description(query),
    }

    # Cast integer values
    try:
        if "page_size" in query:
            query["page_size"] = int(query["page_size"])
    except ValueError:
        del query["page_size"]

    try:
        if "page" in query:
            query["page"] = int(query["page"])
    except ValueError:
        del query["page"]

    # Maximum amount of articles returned in a single page: 100
    if "page_size" not in query:
        query["page_size"] = 100

    try:
        logging.debug("query: %r", query)
        articles = newsapi_getters[subset](**query)
        logging.debug("total amount of articles: %d", articles["totalResults"])
    except (ValueError, TypeError) as e:
        logging.error("invalid request: %s", e)
        abort(401, "an error occurred while fetching the articles")
    except NewsAPIException as e:
        logging.error("couldn't query the API: %s", e)
        abort(401, "an error occurred while fetching the articles")

    return feed_types[feed_type](newsapi._sources_cache, query, query_meta, articles["articles"])


class CliOptions(argparse.Namespace):
    def __init__(self, args):
        parser = argparse.ArgumentParser(description="News2RSS - An HTTP server that returns feeds of news articles")

        parser.add_argument("-d", "--debug", default=False, action="store_true", help="Display debug messages")
        parser.add_argument("-v", "--verbose", default=False, action="store_true", help="Display more messages")
        parser.add_argument("-H", "--host", default="localhost", help="Hostname to bind to")
        parser.add_argument("-P", "--port", type=int, default=8080, help="Port to listen on")
        parser.add_argument("-X", "--api-key", help="News API authentication key")

        parser.parse_args(args, self)


def main(av):
    cli_options = CliOptions(av[1:])

    logging_level = logging.WARN
    if cli_options.debug:
        logging_level = logging.DEBUG
    elif cli_options.verbose:
        logging_level = logging.INFO
    logging.basicConfig(level=logging_level,
                        format="[%(asctime)s][%(levelname)s]: %(message)s")

    api_key = cli_options.api_key or os.getenv("NEWS2RSS_API_KEY")
    if not api_key:
        logging.critical("No API key set")
        return 1

    bottle.install(NewsAPIPlugin(api_key=api_key))

    bottle.run(host=cli_options.host, port=cli_options.port,
               debug=cli_options.debug, reloader=cli_options.debug)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
