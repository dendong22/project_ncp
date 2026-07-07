import unittest
from unittest.mock import patch

import numpy as np

from modules.embedder_clova import ClovaEmbedder


class ClovaEmbedderFallbackTests(unittest.TestCase):
    def test_embed_documents_falls_back_when_api_fails(self):
        embedder = ClovaEmbedder(api_key="k", apigw_key="g", dim=32)

        with patch("modules.embedder_clova.requests.post", side_effect=Exception("boom")):
            vectors = embedder.embed_documents(["첫 번째 문서", "두 번째 문서"], batch_size=1)

        self.assertEqual(vectors.shape, (2, 32))
        self.assertTrue(np.isfinite(vectors).all())
        self.assertTrue(np.allclose(np.linalg.norm(vectors, axis=1), 1.0))


if __name__ == "__main__":
    unittest.main()
