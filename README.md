# mkdocs-h5p

Embed H5P packages in MkDocs Markdown using
[tunapanda/h5p-standalone](https://github.com/tunapanda/h5p-standalone).

The plugin extracts each referenced `.h5p` file during `mkdocs build` and
injects the standalone H5P player into the generated page by default. It can
also render each H5P package through a generated standalone HTML file embedded
with an iframe.

## Installation

```bash
pip install -e .
```

Enable the plugin in `mkdocs.yml`:

```yaml
plugins:
  - search
  - h5p
```

## Markdown Usage

Use the image-like syntax with `h5p` as the alt text:

```markdown
![h5p](activities/example.h5p)
```

You can also use the explicit shortcode form:

```markdown
{{ h5p("activities/example.h5p") }}
```

Paths are resolved relative to the Markdown file first, then relative to
`docs_dir`.

## Configuration

```yaml
plugins:
  - h5p:
      h5p_dir: assets/h5p
      player_url: https://cdn.jsdelivr.net/npm/h5p-standalone@3.8.0/dist
      render_mode: inline
      frame: true
      full_screen: true
      export: false
      embed: false
      copyright: false
      player_options:
        icon: true
```

`player_url` points to the `dist` directory for `h5p-standalone`. By default it
uses jsDelivr with version `3.8.0` pinned. For offline sites, download the
`h5p-standalone` `dist` files into your MkDocs docs folder and set `player_url`
to that local URL.

`render_mode` can be `inline` or `iframe`. The default, `inline`, adds the H5P
div and `h5p-standalone` scripts to the generated MkDocs page. `iframe` creates
`mkdocs-h5p.html` inside the extracted package directory under `h5p_dir` and
embeds that file in the generated page.

The original `.h5p` package is also copied into the generated site and exposed
to `h5p-standalone` through `downloadUrl`, so enabling `export: true` can provide
a working download button.

When `embed: true` is enabled, the plugin also provides `embedCode` to
`h5p-standalone`. The generated embed code is an iframe that points to the
standalone `mkdocs-h5p.html` file for that activity. Set MkDocs `site_url` to
make the embed iframe use a full absolute URL.

## Automatic Iframe Resizing

The standalone `mkdocs-h5p.html` player page (used by `render_mode: iframe`
and by the `embedCode` snippet) watches its own content height with a
`ResizeObserver` and posts a message to its parent window whenever it
changes:

```js
window.parent.postMessage({ type: 'iframeResize', height: <content height> }, '*')
```

When `render_mode: iframe` is used, the generated MkDocs page automatically
listens for this message and resizes the matching `<iframe class="mkdocs-h5p">`
to fit its content, so the fixed `height:600px` default is only used until the
first resize message arrives.

If you embed the `embedCode` iframe on a different website, that site needs
its own listener for `{ type: 'iframeResize', height }` messages to resize the
iframe — the plugin only controls the listener on pages it generates.

## Notes

Some H5P exports do not include every required library. The standalone player
can only run content when the `.h5p` package includes the libraries it needs.

## Publishing to PyPI

Install the packaging tools:

```bash
python -m pip install --upgrade build twine
```

Update `pyproject.toml` `version` before publishing:

Build the source distribution and wheel:

```bash
python -m build
```

Publish to PyPI:

```bash
python -m twine upload dist/*
```

When prompted by `twine`, use `__token__` as the username and a PyPI API token
as the password.
