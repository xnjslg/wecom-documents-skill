#!/usr/bin/env python3
"""Index images in a saved native Markdown response; download only on request."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import html
import json
from pathlib import Path
import re
import struct
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

PATTERNS = (
    re.compile(r'!\[(?P<alt>[^\]]*)\]\(\s*(?P<url><[^>\n]+>|[^\s)]+)(?:\s+[^)]*)?\)'),
    re.compile(r'<img\b[^>]*\bsrc=[\"\x27](?P<url>[^\"\x27]+)[\"\x27][^>]*>', re.I),
)
MAX_IMAGE_BYTES = 32 * 1024 * 1024


def validate_image_url(url: str) -> None:
    parts = urlsplit(url)
    host = parts.hostname or ''
    if (parts.scheme != 'https' or not host.endswith('.qpic.cn') or parts.username
            or parts.password or parts.port not in (None, 443)):
        raise ValueError('Image URL must use the WeCom qpic.cn HTTPS CDN')


class ImageRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_image_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def image_type(data: bytes) -> tuple[str, int | None, int | None]:
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) >= 24 and data[12:16] == b'IHDR':
        width, height = struct.unpack('>II', data[16:24])
        return 'png', width, height
    if data.startswith((b'GIF87a', b'GIF89a')) and len(data) >= 10:
        width, height = struct.unpack('<HH', data[6:10])
        return 'gif', width, height
    if data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9'):
        return 'jpg', None, None
    if len(data) >= 16 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'webp', None, None
    raise ValueError('Response is not a supported PNG, JPEG, GIF or WebP image')


def references(markdown: str) -> list[dict]:
    lines = markdown.splitlines()
    found = []
    for pattern in PATTERNS:
        for match in pattern.finditer(markdown):
            line = markdown.count('\n', 0, match.start()) + 1
            before = next((value.strip() for value in reversed(lines[max(0, line - 4):line - 1])
                           if value.strip()), '')
            after = next((value.strip() for value in lines[line:line + 3] if value.strip()), '')
            found.append({'url': html.unescape(match.group('url').strip('<>')),
                          'alt': match.groupdict().get('alt') or '', 'line': line,
                          'context_before': before, 'context_after': after,
                          'start': match.start('url'), 'end': match.end('url')})
    return sorted(found, key=lambda item: item['start'])


def download_image(item: dict, directory: Path, timeout: float) -> dict:
    result = dict(item)
    try:
        validate_image_url(item['url'])
        request = Request(item['url'], headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'image/*'})
        with build_opener(ImageRedirects()).open(request, timeout=timeout) as response:
            validate_image_url(response.url)
            data = response.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError('Image exceeded the 32 MiB size limit')
        extension, width, height = image_type(data)
        relative = f'images/{item["id"]:03d}.{extension}'
        with (directory / relative).open('xb') as stream:
            stream.write(data)
        result.update(status='downloaded', local_path=relative, bytes=len(data),
                      sha256=hashlib.sha256(data).hexdigest(), width=width, height=height)
    except Exception as error:
        result.update(status='failed', error_type=type(error).__name__, error=str(error))
    return result


def collect_images(input_path: Path, output: Path, *, download: bool = False,
                   workers: int = 4, timeout: float = 20) -> dict:
    if not 1 <= workers <= 8 or timeout <= 0:
        raise ValueError('Use 1 to 8 workers and a positive timeout')
    envelope = json.loads(input_path.read_text(encoding='utf-8-sig'))
    if not isinstance(envelope, dict) or type(envelope.get('errcode')) is not int or envelope['errcode'] != 0:
        raise ValueError('Expected a successful native Markdown response')
    fields = [key for key in ('content', 'file_path') if key in envelope]
    if len(fields) != 1 or not isinstance(envelope[fields[0]], str):
        raise ValueError('Expected one text content/file_path field, not a filesystem path')
    source = urlsplit(envelope.get('url', ''))
    if source.scheme != 'https' or source.hostname != 'doc.weixin.qq.com' or not re.fullmatch(r'/doc/[^/]+', source.path):
        raise ValueError('Native response must identify its WeCom document')
    markdown = envelope[fields[0]]
    found = references(markdown)
    grouped = {}
    for ref in found:
        if ref['url'] not in grouped:
            grouped[ref['url']] = {'id': len(grouped) + 1, 'url': ref['url'],
                                    'status': 'not_requested', 'references': []}
        grouped[ref['url']]['references'].append({key: ref[key] for key in
                                                ('line', 'alt', 'context_before', 'context_after')})
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    (output / 'document.original.md').write_text(markdown, encoding='utf-8')
    items = list(grouped.values())
    if download:
        (output / 'images').mkdir(mode=0o700)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            items = list(pool.map(lambda item: download_image(item, output, timeout), items))
    by_url = {item['url']: item for item in items}
    local = markdown
    for ref in reversed(found):
        item = by_url[ref['url']]
        if item['status'] == 'downloaded':
            local = local[:ref['start']] + item['local_path'] + local[ref['end']:]
    (output / 'document.local.md').write_text(local, encoding='utf-8')
    downloaded = sum(item['status'] == 'downloaded' for item in items)
    failed = sum(item['status'] == 'failed' for item in items)
    summary = {'status': 'partial' if failed else 'downloaded' if download else 'indexed',
               'download_requested': download, 'image_reference_count': len(found),
               'image_count': len(items), 'downloaded_count': downloaded, 'failed_count': failed,
               'visual_reviewed': False, 'manifest_json': str(output / 'images.json'),
               'local_markdown': str(output / 'document.local.md')}
    manifest = {**summary, 'document_url': envelope['url'], 'title': envelope.get('name'),
                'markdown_sha256': hashlib.sha256(markdown.encode('utf-8')).hexdigest(), 'images': items}
    (output / 'images.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path, help='Saved native Markdown response JSON')
    parser.add_argument('--output', required=True, type=Path, help='A new private output directory')
    parser.add_argument('--download-images', action='store_true', help='Download referenced images in addition to indexing them')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--timeout', type=float, default=20)
    args = parser.parse_args()
    try:
        result = collect_images(args.input, args.output, download=args.download_images,
                                workers=args.workers, timeout=args.timeout)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result['failed_count'] else 0
    except (OSError, ValueError) as error:
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__, 'error': str(error)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
