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

from enum import Enum, auto

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


def _feed_rss(source_meta, articles):
    feed = FeedGenerator()

    if "name" not in source_meta:
        logging.error("no 'name' entry in the source meta")
        abort(401, "an error occurred while generating the feed")
    feed.title(source_meta["name"])

    if "url" not in source_meta:
        logging.error("no 'url' entry in the source meta")
        abort(401, "an error occurred while generating the feed")
    feed.link(href=source_meta["url"], rel="self")

    if "description" not in source_meta:
        logging.error("no 'description' entry in the source meta")
        abort(401, "an error occurred while generating the feed")
    feed.description(source_meta["description"])

    if "id" in source_meta:
        feed.id(source_meta["id"])

    if "category" in source_meta:
        feed.category(term=source_meta["category"])

    if "language" in source_meta:
        feed.language(source_meta["language"])

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

        if "author" in article:
            entry.author(name=article["author"], email="e@ma.il")

        if "url" in article:
            entry.link(href=article["url"], rel="alternate")

        if "publishedAt" in article:
            entry.pubDate(article["publishedAt"])

    try:
        return feed.rss_str()
    except ValueError as e:
        logging.error("invalid data: %s", e)
        abort(401, "an error occurred while generating the feed")


@get("/<feed_type>/<source_id>/<subset>")
def get_feed_sources(feed_type, source_id, subset, newsapi):
    newsapi_getters = {
        "all": newsapi.get_everything,
        "top": newsapi.get_top_headlines,
    }
    source_meta = next((source for source in newsapi._sources_cache if source["id"] == source_id),
                       None)

    if not source_meta:
        abort(401, "invalid source identifier")
    elif subset not in newsapi_getters.keys():
        abort(401, "invalid subset, must be one of: %s" % newsapi_getters.keys())

    try:
        # Maximum amount of articles returned in a single page: 100
        articles = newsapi_getters[subset](sources=source_id, page_size=100)
        logging.debug("total amount of articles: %d", articles["totalResults"])
    except (ValueError, TypeError) as e:
        logging.error("invalid request: %s", e)
        abort(401, "an error occurred while fetching the articles")
    except NewsAPIException as e:
        logging.error("couldn't query the API: %s", e)
        abort(401, "an error occurred while fetching the articles")

    logging.debug("requested feed type: %s", feed_type)

    if feed_type == "rss":
        return _feed_rss(source_meta, articles["articles"])
    else:
        abort(401, "invalid feed type")


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
