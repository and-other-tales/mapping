import unittest
from unittest.mock import patch, MagicMock
import os
from tiles_downloader import fetch_tileset, reproject_and_mosaic, create_xyz_tiles

class TestIntegration(unittest.TestCase):

    @patch('requests.get')
    def test_full_pipeline(self, mock_get):
        # Mock the API response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "root": {
                "children": [
                    {"content": {"uri": "tile_1.json"}},
                    {"content": {"uri": "tile_2.json"}}
                ]
            }
        }
        mock_get.return_value = mock_response

        # Mock session
        session = MagicMock()

        # Step 1: Fetch tileset
        fetch_tileset("https://example.com/tileset.json", session, "output_dir")

        # Step 2: Reproject and mosaic
        mosaic_path = "output_dir/mosaic.tif"
        reproject_and_mosaic("output_dir", mosaic_path)

        # Step 3: Create XYZ tiles
        create_xyz_tiles(mosaic_path, "output_dir/tiles")

        # Assertions
        self.assertTrue(os.path.exists("output_dir"))
        self.assertTrue(os.path.exists(mosaic_path))
        self.assertTrue(os.path.exists("output_dir/tiles"))

if __name__ == "__main__":
    unittest.main()
