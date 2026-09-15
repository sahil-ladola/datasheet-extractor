# Datasheet Extractor

![tests](https://github.com/sahil-ladola/datasheet-extractor/actions/workflows/ci.yml/badge.svg)

Machine builders keep hundreds of component datasheets as PDFs, and nothing in
them can be searched or compared. This tool reads those PDFs, pulls out a fixed
set of specifications, checks them, and shows the result in a table you can filter.

<!-- TODO: docs/screenshot.png once the dashboard exists -->

## Run it

<!-- TODO: three commands, filled in once the pipeline and dashboard exist -->

## How it works

<!-- TODO: short walkthrough of loader -> page selection -> extractor -> validator -> store -->

The LLM client is pluggable. The extractor talks to an abstract `LLMClient`
interface, and the shipped implementation uses Google Gemini. Adding another
provider means writing one small class.

## Limitations

<!-- TODO: scanned PDFs, page-selection heuristic failure mode, measuring range
     stored as text, LLM extraction errors -->

## Development

```
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

The test suite runs offline. The LLM call is replaced by a fake client, so no
API key is needed to run the tests.

## License

MIT, see [LICENSE](LICENSE).
