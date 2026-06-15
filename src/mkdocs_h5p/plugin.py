from __future__ import annotations

import hashlib
import html
import json
import posixpath
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from mkdocs.config import config_options
from mkdocs.exceptions import PluginError
from mkdocs.plugins import BasePlugin
from mkdocs.structure.pages import Page
from mkdocs.utils import get_relative_url


IMAGE_PATTERN = re.compile(r"!\[(?P<alt>h5p)(?P<title>[^\]]*)\]\((?P<path>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
SHORTCODE_PATTERN = re.compile(r"\{\{\s*h5p\(\s*[\"'](?P<path>[^\"']+)[\"']\s*\)\s*\}\}")
SAFE_ID_PATTERN = re.compile(r"[^a-zA-Z0-9_-]+")


@dataclass(frozen=True)
class H5PAsset:
    source: Path
    output_name: str
    player_file: str = "mkdocs-h5p.html"

    @property
    def download_file(self) -> str:
        return f"{self.output_name}.h5p"


class H5PPlugin(BasePlugin):
    config_scheme = (
        ("enabled", config_options.Type(bool, default=True)),
        ("h5p_dir", config_options.Type(str, default="assets/h5p")),
        (
            "player_url",
            config_options.Type(
                str,
                default="https://cdn.jsdelivr.net/npm/h5p-standalone@3.8.0/dist",
            ),
        ),
        ("render_mode", config_options.Choice(("inline", "iframe"), default="inline")),
        ("frame", config_options.Type(bool, default=True)),
        ("full_screen", config_options.Type(bool, default=True)),
        ("export", config_options.Type(bool, default=False)),
        ("embed", config_options.Type(bool, default=False)),
        ("copyright", config_options.Type(bool, default=False)),
        ("player_options", config_options.Type(dict, default={})),
    )

    def on_config(self, config: dict[str, Any]) -> dict[str, Any]:
        self.docs_dir = Path(config["docs_dir"]).resolve()
        self.site_dir = Path(config["site_dir"]).resolve()
        self._page_counter = 0
        return config

    def on_page_markdown(
        self,
        markdown: str,
        *,
        page: Page,
        config: dict[str, Any],
        files: Any,
    ) -> str:
        if not self.config["enabled"]:
            return markdown

        players: list[dict[str, Any]] = []

        def replace_image(match: re.Match[str]) -> str:
            title = match.group("title").strip() or None
            return self._render_player(match.group("path"), page, title, players)

        def replace_shortcode(match: re.Match[str]) -> str:
            return self._render_player(match.group("path"), page, None, players)

        markdown = IMAGE_PATTERN.sub(replace_image, markdown)
        markdown = SHORTCODE_PATTERN.sub(replace_shortcode, markdown)

        if not players:
            return markdown

        return f"{markdown}\n\n{self._render_inline_loader(players, page)}"

    def _render_player(
        self,
        raw_path: str,
        page: Page,
        title: str | None,
        players: list[dict[str, Any]],
    ) -> str:
        source = self._resolve_h5p_path(raw_path, page)
        asset = self._extract_h5p(source)
        label = html.escape(title or source.stem)

        if self.config["render_mode"] == "inline":
            return self._render_inline_player(asset, page, label, players)
        return self._render_iframe_player(asset, page, label)

    def _render_inline_player(
        self,
        asset: H5PAsset,
        page: Page,
        label: str,
        players: list[dict[str, Any]],
    ) -> str:
        player_id = self._player_id(asset.source)
        options = self._player_options(
            player_id=player_id,
            h5p_json_path=self._page_relative_url(page, self._asset_url(asset.output_name)),
            output_name=asset.output_name,
            source_url=page.url,
        )
        players.append({"id": player_id, "options": options})
        return f'<div id="{player_id}" class="mkdocs-h5p" aria-label="{label}"></div>'

    def _render_iframe_player(self, asset: H5PAsset, page: Page, label: str) -> str:
        self._write_standalone_player(asset)
        iframe_src = self._page_relative_url(page, self._standalone_asset_url(asset))
        fullscreen = " allowfullscreen" if self.config["full_screen"] else ""
        return (
            f'<iframe class="mkdocs-h5p" src="{html.escape(iframe_src)}" '
            f'title="{label}" loading="lazy" style="width:100%;height:600px;border:0;"'
            f"{fullscreen}></iframe>"
        )

    def _write_standalone_player(self, asset: H5PAsset) -> None:
        player_id = "mkdocs-h5p-player"
        standalone_url = self._standalone_asset_url(asset)
        options = self._player_options(
            player_id=player_id,
            h5p_json_path=".",
            output_name=asset.output_name,
            source_url=standalone_url,
        )

        destination = self.site_dir / self.config["h5p_dir"] / asset.output_name / asset.player_file
        destination.write_text(
            self._render_standalone_html(
                player_id=player_id,
                main_bundle=self._relative_url(standalone_url, self._player_asset_url("main.bundle.js")),
                options=options,
            ),
            encoding="utf-8",
        )

    def _player_options(
        self,
        *,
        player_id: str,
        h5p_json_path: str,
        output_name: str,
        source_url: str,
    ) -> dict[str, Any]:
        options = {
            **self.config["player_options"],
            "id": player_id,
            "h5pJsonPath": h5p_json_path,
            "downloadUrl": self._relative_url(source_url, self._download_asset_url(output_name)),
            "frameJs": self._relative_url(source_url, self._player_asset_url("frame.bundle.js")),
            "frameCss": self._relative_url(source_url, self._player_asset_url("styles/h5p.css")),
            "frame": self.config["frame"],
            "fullScreen": self.config["full_screen"],
            "export": self.config["export"],
            "embed": self.config["embed"],
            "copyright": self.config["copyright"],
        }
        return options

    def _resolve_h5p_path(self, raw_path: str, page: Page) -> Path:
        raw_path = raw_path.split("#", 1)[0].split("?", 1)[0]
        path = Path(raw_path)
        candidates = []

        if path.is_absolute():
            candidates.append(path)
        else:
            candidates.append(Path(page.file.abs_src_path).parent / path)
            candidates.append(self.docs_dir / path)

        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved.is_file():
                if resolved.suffix.lower() != ".h5p":
                    raise PluginError(f"H5P reference is not a .h5p file: {raw_path}")
                return resolved

        raise PluginError(f"H5P file not found: {raw_path}")

    def _extract_h5p(self, source: Path) -> H5PAsset:
        digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
        output_name = f"{_slug(source.stem)}-{digest}"
        destination = self.site_dir / self.config["h5p_dir"] / output_name

        if not destination.exists():
            destination.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(source) as archive:
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    member_path = Path(member.filename)
                    if member_path.is_absolute() or ".." in member_path.parts:
                        raise PluginError(f"Unsafe path in H5P archive {source}: {member.filename}")
                    target = destination / member_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)

        asset = H5PAsset(source=source, output_name=output_name)
        shutil.copyfile(source, destination / asset.download_file)
        return asset

    def _render_inline_loader(self, players: list[dict[str, Any]], page: Page) -> str:
        main_bundle = self._page_relative_url(page, self._player_asset_url("main.bundle.js"))
        payload = json.dumps(players, separators=(",", ":")).replace("</", "<\\/")
        return (
            f'<script src="{html.escape(main_bundle)}" charset="UTF-8"></script>\n'
            "<script>\n"
            "(function(){\n"
            f"  var players = {payload};\n"
            "  function init(){\n"
            "    var H5P = window.H5PStandalone && window.H5PStandalone.H5P;\n"
            "    if (!H5P) {\n"
            "      console.error('mkdocs-h5p: h5p-standalone failed to load');\n"
            "      return;\n"
            "    }\n"
            "    (async function(){\n"
            "      for (var index = 0; index < players.length; index += 1) {\n"
            "        var player = players[index];\n"
            "        var element = document.getElementById(player.id);\n"
            "        if (!element) continue;\n"
            "        try {\n"
            "          await new H5P(element, player.options);\n"
            "        } catch (error) {\n"
            "          console.error('mkdocs-h5p: failed to initialize H5P player', player.id, error);\n"
            "        }\n"
            "      }\n"
            "    }());\n"
            "  }\n"
            "  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);\n"
            "  else init();\n"
            "}());\n"
            "</script>"
        )

    def _render_standalone_html(
        self,
        *,
        player_id: str,
        main_bundle: str,
        options: dict[str, Any],
    ) -> str:
        payload = json.dumps(options, separators=(",", ":")).replace("</", "<\\/")
        return (
            "<!doctype html>\n"
            '<html lang="en">\n'
            "<head>\n"
            '  <meta charset="utf-8">\n'
            '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
            "</head>\n"
            "<body>\n"
            f'  <div id="{html.escape(player_id)}" class="mkdocs-h5p"></div>\n'
            f'  <script src="{html.escape(main_bundle)}" charset="UTF-8"></script>\n'
            "  <script>\n"
            "  (function(){\n"
            f"    var options = {payload};\n"
            "    function init(){\n"
            "      var H5P = window.H5PStandalone && window.H5PStandalone.H5P;\n"
            "      if (!H5P) {\n"
            "        console.error('mkdocs-h5p: h5p-standalone failed to load');\n"
            "        return;\n"
            "      }\n"
            "      var element = document.getElementById(options.id);\n"
            "      if (!element) return;\n"
            "      try {\n"
            "        Promise.resolve(new H5P(element, options)).catch(function(error){\n"
            "          console.error('mkdocs-h5p: failed to initialize H5P player', options.id, error);\n"
            "        });\n"
            "      } catch (error) {\n"
            "        console.error('mkdocs-h5p: failed to initialize H5P player', options.id, error);\n"
            "      }\n"
            "    }\n"
            "    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);\n"
            "    else init();\n"
            "  }());\n"
            "  </script>\n"
            "</body>\n"
            "</html>\n"
        )

    def _asset_url(self, output_name: str) -> str:
        return _join_url(self.config["h5p_dir"], output_name)

    def _download_asset_url(self, output_name: str) -> str:
        return _join_url(self._asset_url(output_name), f"{output_name}.h5p")

    def _standalone_asset_url(self, asset: H5PAsset) -> str:
        return _join_url(self._asset_url(asset.output_name), asset.player_file)

    def _player_asset_url(self, filename: str) -> str:
        return _join_url(self.config["player_url"], filename)

    def _page_relative_url(self, page: Page, target: str) -> str:
        return self._relative_url(page.url, target)

    def _relative_url(self, source_url: str, target: str) -> str:
        if _is_absolute_url(target) or target.startswith("/"):
            return target
        return get_relative_url(target, source_url)

    def _player_id(self, source: Path) -> str:
        self._page_counter += 1
        seed = f"{source}:{self._page_counter}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:10]
        return f"mkdocs-h5p-{digest}"


def _slug(value: str) -> str:
    slug = SAFE_ID_PATTERN.sub("-", value.strip()).strip("-").lower()
    return slug or "content"


def _join_url(base: str, path: str) -> str:
    if _is_absolute_url(base):
        return f"{base.rstrip('/')}/{quote(path, safe='/')}"
    return posixpath.join(base.rstrip("/"), quote(path, safe="/"))


def _is_absolute_url(value: str) -> bool:
    return value.startswith(("http://", "https://", "//"))
