import unittest
from unittest.mock import patch, MagicMock
import os
import json
from tiles_downloader import fetch_tileset, process_child_json, extract_textures

class Test3DTiles(unittest.TestCase):

    @patch('requests.get')
    def test_fetch_tileset(self, mock_get):
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

        # Call the function
        session = MagicMock()
        fetch_tileset("https://example.com/tileset.json", session, "output_dir")

        # Assertions
        mock_get.assert_called()
        self.assertTrue(os.path.exists("output_dir"))

    def test_process_child_json(self):
        # Mock JSON data
        json_data = {
            "children": [
                {"content": {"uri": "tile_1.json"}},
                {"content": {"uri": "tile_2.json"}}
            ]
        }

        # Mock session
        session = MagicMock()

        # Call the function
        process_child_json(json_data, session, "output_dir")

        # Assertions
        self.assertTrue(os.path.exists("output_dir"))

    def test_extract_textures(self):
        # Mock a GLTF file
        with open("mock_tile.glb", "wb") as f:
            f.write(b"mock data")

        # Call the function
        extract_textures("mock_tile.glb", "output_dir")

        # Assertions
        self.assertTrue(os.path.exists("output_dir"))

if __name__ == "__main__":
    unittest.main()
