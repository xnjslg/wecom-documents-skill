"""Image indexing must not download unless explicitly requested."""
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import MagicMock, patch

SCRIPT = Path(__file__).resolve().parents[1] / 'document_images.py'
spec = importlib.util.spec_from_file_location('document_images', SCRIPT)
images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(images)


class DocumentImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wecom-images-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'markdown.json'
        self.url = 'https://wdcdn.qpic.cn/example?w=20&h=10'
        self.markdown = f'# 标题\n\n![流程]({self.url})\n图 1\n\n![重复]({self.url})\n'
        self.source.write_text(json.dumps({'errcode': 0, 'content': self.markdown,
                                          'url': 'https://doc.weixin.qq.com/doc/w3_test'}), encoding='utf-8')

    def test_default_only_indexes_and_preserves_remote_references(self):
        with patch.object(images, 'build_opener') as network:
            result = images.collect_images(self.source, self.root / 'output')
            network.assert_not_called()
        self.assertFalse(result['download_requested'])
        self.assertEqual(result['status'], 'indexed')
        self.assertEqual(result['image_count'], 1)
        self.assertEqual(result['image_reference_count'], 2)
        self.assertEqual(Path(result['local_markdown']).read_text(), self.markdown)

    def test_download_reuses_one_image_and_keeps_source_locations(self):
        png = b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\x0dIHDR' + struct.pack('>II', 20, 10)
        response = MagicMock()
        response.__enter__.return_value = response
        response.url = self.url
        response.read.return_value = png
        with patch.object(images, 'build_opener') as network:
            network.return_value.open.return_value = response
            result = images.collect_images(self.source, self.root / 'output', download=True)
            self.assertEqual(network.return_value.open.call_count, 1)
        self.assertEqual(result['downloaded_count'], 1)
        self.assertEqual(result['failed_count'], 0)
        manifest = json.loads(Path(result['manifest_json']).read_text())
        image = manifest['images'][0]
        self.assertEqual((image['width'], image['height']), (20, 10))
        self.assertEqual([ref['line'] for ref in image['references']], [3, 6])
        self.assertEqual(image['references'][0]['context_after'], '图 1')
        self.assertEqual(Path(result['local_markdown']).read_text().count('images/001.png'), 2)
        self.assertFalse(result['visual_reviewed'])

    def test_non_image_response_is_reported_without_losing_body(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.url = self.url
        response.read.return_value = b'<html>login required</html>'
        with patch.object(images, 'build_opener') as network:
            network.return_value.open.return_value = response
            result = images.collect_images(self.source, self.root / 'output', download=True)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['failed_count'], 1)
        self.assertEqual(result['downloaded_count'], 0)
        self.assertEqual(Path(result['local_markdown']).read_text(), self.markdown)

    def test_untrusted_destination_is_not_requested(self):
        with patch.object(images, 'build_opener') as network:
            result = images.download_image({'id': 1, 'url': 'https://127.0.0.1/secret'}, self.root, 1)
            network.assert_not_called()
        self.assertEqual(result['status'], 'failed')

    def test_html_reference_keeps_escaped_query_and_line(self):
        refs = images.references('<img src="https://wdcdn.qpic.cn/x?w=1&amp;h=2">')
        self.assertEqual(refs[0]['url'], 'https://wdcdn.qpic.cn/x?w=1&h=2')
        self.assertEqual(refs[0]['line'], 1)


if __name__ == '__main__':
    unittest.main()
