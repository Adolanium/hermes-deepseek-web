<div align="center">

  <a href="https://github.com/NousResearch/hermes-agent">
    <img src="https://github.com/user-attachments/assets/ac2f5702-c842-4b2e-9340-737481fa0ece" width="96" height="96" alt="Nous Research Hermes mark" />
  </a>

  # Hermes DeepSeek Web

  **Search and read the live web with the DeepSeek key you already have.**

  Hermes DeepSeek Web plugs [DeepSeek Search & Fetch](https://api-docs.deepseek.com/guides/search) into the `web_search` and `web_extract` tools Hermes already has.
  Search returns ranked results with model-ready summaries. Fetch returns any page as Markdown.

  <sub>POWERED BY <a href="https://github.com/NousResearch/hermes-agent">HERMES AGENT</a> &nbsp;·&nbsp; COMMUNITY PLUGIN &nbsp;·&nbsp; VERSION 0.1.0</sub>

  <br /><br />

  [What it does](#search-and-fetch) &nbsp;·&nbsp; [Install it](#make-it-yours) &nbsp;·&nbsp; [Where your data goes](#privacy-you-can-explain-in-one-breath)

</div>

![A marble Hermes lowers a line into deep water and pulls up a search-result window beside a fetched page](assets/catalog-banner.png)

## Powered by Hermes

Hermes DeepSeek Web is a community-built web backend for [Hermes Agent](https://github.com/NousResearch/hermes-agent). It adds no tools of its own. It registers a provider named `deepseek` through Hermes's web provider API, so the agent keeps calling `web_search` and `web_extract` exactly as before, and Hermes routes those calls to DeepSeek when you choose it.

It works in the CLI, Hermes Desktop, the messaging gateway, cron jobs and subagents: anywhere the web tools run.

## Search and fetch

| | |
| --- | --- |
| **Search**<br />`web_search` calls DeepSeek Search and returns titles, URLs and DeepSeek's long summary of each result, written for models to read. | **Read**<br />`web_extract` calls DeepSeek Fetch for each URL and returns the page as Markdown, with its title. |
| **One key**<br />Uses `DEEPSEEK_API_KEY`, the same key Hermes uses for DeepSeek models. | **Fail per page**<br />One broken URL comes back with its own error. The rest of the batch still arrives. |

## Small decisions, already made

- Results carry DeepSeek's long summary (about 600 to 800 characters), falling back to the short snippet when a result has no summary. Full page text is not requested, to save tokens.
- Search returns at most 50 results, DeepSeek's maximum. Price is per request, not per result.
- Queries longer than 128 characters, DeepSeek's limit, are refused with a message asking the agent to shorten them. No request is sent, so nothing is billed.
- Only `http://` and `https://` URLs of up to 8,192 characters, without a username or password, are sent to Fetch. Anything else is refused before a request is made.
- Several URLs are read in parallel, 3 at a time, to stay under DeepSeek's limit of 5 requests a second.
- DeepSeek's query operators work as written: `site:`, `-site:`, `"exact phrase"`, `-term`, `lang:zh,en`, `after:2025-01-01`, `before:2026-06-01`.
- Errors say what to do next: check the key, top up the balance, wait out the rate limit, or check DeepSeek's status page.
- Each request times out after 60 seconds.

## Make it yours

### Install

```sh
hermes plugins install Adolanium/hermes-deepseek-web
```

The installer asks for `DEEPSEEK_API_KEY` unless Hermes already has it.

Hermes DeepSeek Web needs Hermes Agent 0.21.6 or later.

### Choose it

Pick **DeepSeek Search** in `hermes tools`, or set the backends in `~/.hermes/config.yaml`:

```yaml
web:
  search_backend: deepseek   # web_search through DeepSeek Search
  extract_backend: deepseek  # web_extract through DeepSeek Fetch
```

Set only one of them to keep your current provider for the other.

If you set neither and DeepSeek is the only web backend with a key, Hermes uses it on its own. Because many Hermes users already have a DeepSeek key for models, set `web.search_backend` and `web.extract_backend` explicitly if you want a different backend.

### Add a key

Get one at [platform.deepseek.com/api_keys](https://platform.deepseek.com/api_keys). If you already use DeepSeek models in Hermes, there is nothing to add.

Set it with `hermes tools`, or as `DEEPSEEK_API_KEY` in your Hermes environment. The plugin reads it through Hermes's secret scope, so each profile keeps its own key.

### What it costs

DeepSeek charges $0.75 per 1,000 successful requests, for Search and for Fetch, from your DeepSeek balance. Failed requests, empty results and timeouts are not billed. See [DeepSeek's billing notes](https://api-docs.deepseek.com/guides/search).

### Test

The tests use Hermes's real plugin discovery with a mocked HTTP transport, so they make no network calls and cost nothing:

```sh
HERMES_SOURCE=/path/to/hermes-agent bash scripts/test.sh
```

Check the plugin the same way the catalog does:

```sh
hermes plugins validate . --install-deps
```

CI runs both on every push, against Hermes 0.21.6 and 0.22.2 on Python 3.11 and 3.13.

## Privacy you can explain in one breath

```text
Hermes web_search / web_extract  →  api.deepseek.com (DeepSeek)  →  the results or the page
```

- **What leaves your machine.** The agent's search queries and the URLs it reads go to `api.deepseek.com`. DeepSeek runs the search and fetches the pages on its own servers. See [DeepSeek's privacy policy](https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html).
- **Your key.** Sent to DeepSeek as a bearer token. The plugin reads it only through Hermes. It never stores, logs or writes it, and it is never echoed back in an error.
- **Nothing else.** No files read or written, no shell commands, no background processes, no telemetry.

## Compatibility

| Hermes Agent | Python | Status |
| --- | --- | --- |
| 0.22.2 | 3.11, 3.13, 3.14 | Tested in CI and by hand |
| 0.21.6 (minimum) | 3.11, 3.13 | Tested in CI |

The plugin depends only on `httpx`, which Hermes already ships.

## Limits

- Both search and fetch need a DeepSeek key with a positive balance.
- DeepSeek allows 5 search and 5 fetch requests a second per account, across all your keys. Fetch is also limited per site, so many URLs from one site can hit a rate limit.
- Pages behind a login, a paywall, a bot wall, or DeepSeek's security policy come back as a per-page error, not a crash.
- DeepSeek has no batch fetch endpoint, so a long list of URLs is read a few at a time.

<br />

<div align="center">
  <strong>Hermes DeepSeek Web</strong><br />
  <sub>The live web, through the key you already have.</sub>
</div>

<br />

## License

[MIT](LICENSE).

> **Community project**
>
> Hermes DeepSeek Web is an independent community plugin. It is not affiliated with, endorsed by, sponsored by, or officially associated with [Nous Research](https://github.com/NousResearch), the [Hermes Agent project](https://github.com/NousResearch/hermes-agent), or [DeepSeek](https://www.deepseek.com). Hermes, Hermes Agent, Nous Research and DeepSeek are names and marks belonging to their respective owners.
