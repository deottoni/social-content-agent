#!/usr/bin/env python3
"""Substitute {{TOKEN}} placeholders in an HTML file with base64 data URIs,
without ever routing the encoded bytes through the conversation.

Usage:
    python3 embed_assets.py <template.html> <output.html> TOKEN=path/to/image.png [TOKEN2=path2 ...]

Each TOKEN must appear in the template as {{TOKEN}}. The matching file's
mime type is guessed from its extension.
"""
import base64
import mimetypes
import sys


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    template_path, output_path = sys.argv[1], sys.argv[2]
    pairs = [arg.split("=", 1) for arg in sys.argv[3:]]

    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()

    for token, path in pairs:
        mime, _ = mimetypes.guess_type(path)
        mime = mime or "application/octet-stream"
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        placeholder = "{{" + token + "}}"
        if placeholder not in html:
            print(f"warning: placeholder {placeholder} not found in template", file=sys.stderr)
        html = html.replace(placeholder, f"data:{mime};base64,{b64}")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"wrote {output_path} ({len(html)} chars)")


if __name__ == "__main__":
    main()
