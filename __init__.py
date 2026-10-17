"""DeepSeek Search & Fetch backend for Hermes' web_search and web_extract tools."""


def register(ctx):
    from .provider import DeepSeekWebSearchProvider

    ctx.register_web_search_provider(DeepSeekWebSearchProvider())
