import unittest
from unittest.mock import patch, MagicMock
import os
import json
from 3dtiles import fetch_tileset, process_child_json, extract_textures

class Test3DTiles(unittest.TestCase):

    @patch('requests.Session.get')
    def test_fetch_tileset(self, mock_get):
        # Mock the API response for the root tileset
        mock_response_root = MagicMock()
        mock_response_root.json.return_value = {
            "root": {
                "children": [
                    {"content": {"uri": "tile_1.json"}},
                    {"content": {"uri": "tile_2.json"}}
                ]
            }
        }
        # Mock the API response for a child tileset
        mock_response_child = MagicMock()
        mock_response_child.json.return_value = {
            "children": [
                {"content": {"uri": "tile_1.glb"}}
            ]
        }
        # Mock the API response for a .glb file (should return raw data, not JSON)
        mock_response_glb = MagicMock()
        mock_response_glb.json.side_effect = Exception("Not a JSON file")
        mock_response_glb.raw = MagicMock()
        mock_response_glb.raw.read = MagicMock(return_value=b"mock glb data")
        # Set up side effects for each call
        mock_get.side_effect = [mock_response_root, mock_response_child, mock_response_glb]

        # Mock session with cookies
        session = MagicMock()
        session.cookies.get.return_value = "mock_session_id"

        # Call the function
        fetch_tileset("https://example.com/tileset.json", session, "output_dir", api_key="mock_api_key")

        # Assertions
        self.assertTrue(os.path.exists("output_dir"))
        # Check that a .glb file was attempted to be downloaded
        called_urls = [call[0][0] for call in mock_get.call_args_list]
        self.assertTrue(any(url.endswith(".glb?key=mock_api_key&session=mock_session_id") or ".glb&key=mock_api_key&session=mock_session_id" in url for url in called_urls))

    @patch('requests.Session.get')
    def test_process_child_json(self, mock_get):
        # Mock JSON data with a .glb leaf
        json_data = {
            "children": [
                {"content": {"uri": "tile_1.glb"}}
            ]
        }

        # Mock session with cookies
        session = MagicMock()
        session.cookies.get.return_value = "mock_session_id"

        # Mock the API response for a .glb file (should return raw data, not JSON)
        mock_response_glb = MagicMock()
        mock_response_glb.json.side_effect = Exception("Not a JSON file")
        mock_response_glb.raw = MagicMock()
        mock_response_glb.raw.read = MagicMock(return_value=b"mock glb data")
        mock_get.return_value = mock_response_glb

        # Call the function
        process_child_json(json_data, session, "output_dir", api_key="mock_api_key")

        # Assertions
        self.assertTrue(os.path.exists("output_dir"))
        called_url = mock_get.call_args[0][0]
        self.assertIn("key=mock_api_key", called_url)
        self.assertIn("session=mock_session_id", called_url)
        self.assertTrue(".glb" in called_url)

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
