"""Video Watcher - search many video sites at once and watch in one place."""

from __future__ import annotations

from flask import Flask, jsonify, render_template, request

from .providers import build_providers
from .search import SearchAggregator

MAX_QUERY_LEN = 200
MAX_LIMIT = 25


def create_app(aggregator: SearchAggregator | None = None) -> Flask:
    app = Flask(__name__)
    agg = aggregator or SearchAggregator(build_providers())
    app.extensions["aggregator"] = agg

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            providers=[p.info() for p in agg.providers.values()],
            query=request.args.get("q", ""),
        )

    @app.get("/api/sources")
    def sources():
        return jsonify([p.info() for p in agg.providers.values()])

    @app.get("/api/search")
    def search():
        query = (request.args.get("q") or "").strip()
        if not query:
            return jsonify({"error": "missing query parameter 'q'"}), 400
        if len(query) > MAX_QUERY_LEN:
            return jsonify({"error": f"query longer than {MAX_QUERY_LEN} characters"}), 400
        try:
            limit = max(1, min(MAX_LIMIT, int(request.args.get("limit", 8))))
        except ValueError:
            return jsonify({"error": "'limit' must be an integer"}), 400
        raw = request.args.get("sources")
        sources = [s for s in raw.split(",") if s] if raw else None
        return jsonify(agg.search(query, limit=limit, sources=sources))

    return app
