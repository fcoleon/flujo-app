import unittest

from descriptions import sanitize_description


class DescriptionTests(unittest.TestCase):
    def test_code_block_preserves_text_and_strips_attributes(self):
        self.assertEqual(
            sanitize_description('<pre onclick="bad()"><code>  x &lt; 2\n  y &amp; z</code></pre>'),
            '<pre><code>  x &lt; 2\n  y &amp; z</code></pre>',
        )
