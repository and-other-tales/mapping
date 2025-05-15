import unittest
from unittest.mock import patch, MagicMock
import os
import json
from 3dtiles import fetch_tileset, process_child_json, extract_textures

class Test3DTiles(unittest.TestCase):

    @patch('requests.Session.get')
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

        # Mock session with cookies
        session = MagicMock()
        session.cookies.get.return_value = "mock_session_id"

        # Call the function
        fetch_tileset("https://example.com/tileset.json", session, "output_dir", api_key="mock_api_key")

        # Assertions
        mock_get.assert_called()
        called_url = mock_get.call_args[0][0]
        self.assertIn("key=mock_api_key", called_url)
        self.assertIn("session=mock_session_id", called_url)

    @patch('requests.Session.get')
    def test_process_child_json(self, mock_get):
        # Mock JSON data
        json_data = {
            "children": [
                {"content": {"uri": "tile_1.json"}},
                {"content": {"uri": "tile_2.json"}}
            ]
        }

        # Mock session with cookies
        session = MagicMock()
        session.cookies.get.return_value = "mock_session_id"

        # Mock the API response
        mock_response = MagicMock()
        mock_response.json.return_value = {}
        mock_get.return_value = mock_response

        # Call the function
        process_child_json(json_data, session, "output_dir", api_key="mock_api_key")

        # Assertions
        mock_get.assert_called()
        called_url = mock_get.call_args[0][0]
        self.assertIn("key=mock_api_key", called_url)
        self.assertIn("session=mock_session_id", called_url)

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
