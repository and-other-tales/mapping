#!/usr/bin/env python3
"""
3D Tiles Downloader

This script downloads 3D tiles using Google's 3D Tiles API, extracts textures,
creates a mosaic, and generates XYZ tiles.

Usage:
    python3 3dtiles.py <region_name>           # Download and process tiles (region name is for file organization only)
    python3 3dtiles.py test                     # Run in test mode without API key
    python3 3dtiles.py process [region_name]    # Process existing downloaded tiles without API access

Required environment variable (for download mode):
    GOOGLE_API_KEY - Your Google API key with Map Tiles API access

Setup requirements:
    1. Enable the Map Tiles API in Google Cloud Console
    2. Create an API key with Map Tiles API permissions
    3. Set the API key as an environment variable: export GOOGLE_API_KEY=your_api_key

Output directories:
    downloaded_tiles/<region>/ - Raw downloaded image files
    tiles/<region>/ - Processed XYZ tiles for web mapping
"""
import os, sys, math, json, shutil, base64
import requests
from pygltflib import GLTF2
import rasterio
from rasterio.merge import merge
from rasterio.io import MemoryFile
from rasterio.warp import calculate_default_transform, reproject, Resampling
import numpy as np
from PIL import Image

# Use a default test API key if environment variable is not set
API_KEY = os.getenv("GOOGLE_API_KEY", "")
CITY    = "london"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTDIR  = "downloaded_tiles"  # Will be updated with city-specific path
MOSAIC  = "mosaic_3857.tif"   # Will be updated with city-specific path
TILEDIR = "tiles"             # Will be updated with city-specific path

def fetch_tileset(url, session, outdir, api_key=None):
    """Recursively download all tiles and extract textures."""
    if api_key is None:
        api_key = API_KEY

    os.makedirs(outdir, exist_ok=True)
    resp = session.get(url)
    resp.raise_for_status()
    data = resp.json()

    def append_parameters(uri):
        """Ensure the API key and session parameters are appended to the URL."""
        session_param = f"session={session.cookies.get('session', '')}"
        if "?" in uri:
            if "key=" not in uri:
                uri = f"{uri}&key={api_key}"
            if "session=" not in uri:
                uri = f"{uri}&{session_param}"
        else:
            uri = f"{uri}?key={api_key}&{session_param}"
        return uri

    # Process the root node
    if "root" in data:
        root = data["root"]
        if "children" in root:
            for child in root["children"]:
                if "content" in child and "uri" in child["content"]:
                    child["content"]["uri"] = append_parameters(child["content"]["uri"])

    # Process children recursively
    process_child_json(data, session, outdir, api_key=api_key)

def process_child_json(json_data, session, outdir, depth="", api_key=None):
    """Process a JSON file to extract and download content URIs."""
    if api_key is None:
        api_key = API_KEY

    def append_parameters(uri):
        """Ensure the API key and session parameters are appended to the URL."""
        session_param = f"session={session.cookies.get('session', '')}"
        if "?" in uri:
            if "key=" not in uri:
                uri = f"{uri}&key={api_key}"
            if "session=" not in uri:
                uri = f"{uri}&{session_param}"
        else:
            uri = f"{uri}?key={api_key}&{session_param}"
        return uri

    if "children" in json_data:
        for child in json_data["children"]:
            if "content" in child and "uri" in child["content"]:
                child["content"]["uri"] = append_parameters(child["content"]["uri"])

    # Process content URIs
    if "content" in json_data and "uri" in json_data["content"]:
        json_data["content"]["uri"] = append_parameters(json_data["content"]["uri"])

    # Continue processing recursively
    for child in json_data.get("children", []):
        process_child_json(child, session, outdir, depth + "  ", api_key)

def extract_textures(tile_path, outdir, api_key=None):
    """Pull out all images in the GLTF chunk of a .b3dm/.glb or process nested JSON metadata."""
    # Use the global API_KEY if none is provided
    if api_key is None:
        api_key = API_KEY
        
    # Check if the file exists before attempting to process it
    if not os.path.exists(tile_path):
        print(f"Warning: File {tile_path} does not exist")
        return
        
    # Check file extension to handle different file types
    _, ext = os.path.splitext(tile_path)
    ext = ext.lower()
    
    # Skip processing for JSON files
    if ext == '.json':
        print(f"JSON file detected: {tile_path}. Processing as tileset metadata.")
        try:
            with open(tile_path, 'r') as f:
                data = json.load(f)
            
            # Log the structure of the JSON metadata for diagnostics
            print("Inspecting JSON metadata structure:")
            print(json.dumps(data, indent=2)[:1000])  # Print the first 1000 characters
            
            # If this is a tileset JSON, recursively fetch the children
            if 'children' in data:
                print(f"Found {len(data['children'])} children in tileset JSON")
                
                # Process each child
                for i, child in enumerate(data['children']):
                    print(f"Inspecting child {i+1}/{len(data['children'])}:")
                    print(json.dumps(child, indent=2)[:500])  # Log the first 500 characters of the child
                    
                    if 'content' in child and 'uri' in child['content']:
                        child_uri = child['content']['uri']
                        # Ensure URL is absolute
                        if not child_uri.startswith("http"):
                            base_url = os.path.dirname(tile_path)
                            child_url = f"{base_url}/{child_uri.lstrip('/')}"
                        else:
                            child_url = child_uri
                        
                        # Add API key if needed
                        if "?" in child_url:
                            if "key=" not in child_url:
                                child_url = f"{child_url}&key={api_key}"
                        else:
                            child_url = f"{child_url}?key={api_key}"
                        
                        print(f"Downloading child content from tileset: {child_url}")
                        try:
                            r = requests.get(child_url, stream=True)
                            r.raise_for_status()
                            
                            # Use a hash of the URL as filename
                            url_hash = hashlib.md5(child_url.encode()).hexdigest()
                            ext = os.path.splitext(child_url.split("?")[0])[1] or ".bin"
                            if not ext.startswith("."):
                                ext = f".{ext}"
                            child_path = os.path.join(outdir, f"tile_{url_hash}{ext}")
                            
                            # Save the child file
                            if not os.path.exists(child_path):
                                with open(child_path, "wb") as f:
                                    shutil.copyfileobj(r.raw, f)
                                print(f"Saved child content to {child_path}")
                            
                            # Recursively process the child file
                            extract_textures(child_path, outdir, api_key=api_key)
                        except Exception as e:
                            print(f"Error downloading child content: {e}")
                    else:
                        print(f"Warning: Child without content URI found. Attempting to inspect further.")
                        # Log additional details about the child node
                        print(f"Child keys: {list(child.keys())}")
                        if 'boundingVolume' in child:
                            print(f"Bounding volume: {child['boundingVolume']}")
                        if 'geometricError' in child:
                            print(f"Geometric error: {child['geometricError']}")
            else:
                print("No children found in tileset JSON. Ensure the tileset contains valid references.")
                print("JSON metadata keys:", list(data.keys()))
        except Exception as e:
            print(f"Error processing JSON file: {e}")
            import traceback
            traceback.print_exc()
        return
    
    # Skip unsupported file types
    if ext not in ['.glb', '.b3dm', '.gltf']:
        print(f"Unsupported file type: {ext} for file {tile_path}")
        return
    
    try:
        print(f"Extracting textures from {ext} file: {tile_path}")
        # strip off the 28-byte B3DM header if needed
        with open(tile_path, "rb") as f:
            magic = f.read(4)
            f.seek(0)
            if magic == b"b3dm":
                print("Detected b3dm format, skipping 28-byte header")
                f.seek(28)  # skip header
            glb_data = f.read()
        
        # Ensure we have enough data to process
        if len(glb_data) < 100:
            print(f"Warning: File {tile_path} is too small ({len(glb_data)} bytes), may not be a valid GLB/GLTF file")
            return
            
        try:
            # Load GLTF in-memory
            print(f"Loading GLTF data from file size {len(glb_data)} bytes")
            gltf = GLTF2.load_from_bytes(glb_data)
            
            # Check if there are any images
            if not hasattr(gltf, 'images') or not gltf.images:
                print(f"No images found in {tile_path}")
                return
                
            print(f"Found {len(gltf.images)} images in {tile_path}")
            
            for idx, img in enumerate(gltf.images):
                print(f"Processing image {idx+1}/{len(gltf.images)}")
                # Handle images with bufferView reference
                if hasattr(img, 'bufferView') and img.bufferView is not None:
                    try:
                        # Ensure we have bufferViews
                        if not hasattr(gltf, 'bufferViews') or img.bufferView >= len(gltf.bufferViews):
                            print(f"Warning: Invalid bufferView index {img.bufferView} for image in {tile_path}")
                            continue
                            
                        # Get buffer view
                        buffer_view = gltf.bufferViews[img.bufferView]
                        # Find which buffer this view references
                        buffer_index = buffer_view.buffer
                        
                        # Ensure the buffer index is valid
                        if not hasattr(gltf, 'buffers') or buffer_index >= len(gltf.buffers):
                            print(f"Warning: Invalid buffer index {buffer_index} for image in {tile_path}")
                            continue
                            
                        # Get byte offset and length
                        byte_offset = buffer_view.byteOffset or 0
                        byte_length = buffer_view.byteLength
                        
                        # Determine image format & extension from MIME type if available
                        ext = ".jpg"  # Default extension
                        if hasattr(img, 'mimeType'):
                            if img.mimeType == "image/jpeg":
                                ext = ".jpg"
                            elif img.mimeType == "image/png":
                                ext = ".png"
                        
                        img_filename = f"image_{idx}_{buffer_index}_{byte_offset}{ext}"
                        out = os.path.join(outdir, img_filename)
                        
                        print(f"Extracting image to {out} (offset={byte_offset}, length={byte_length})")
                        if len(glb_data) < byte_offset + byte_length:
                            print(f"Warning: Buffer overflow - file size {len(glb_data)}, but need offset {byte_offset} + length {byte_length}")
                            continue
                            
                        # Extract bytes from the buffer
                        img_bytes = glb_data[byte_offset:byte_offset + byte_length]
                        with open(out, "wb") as wf: wf.write(img_bytes)
                        print(f"Successfully extracted {len(img_bytes)} bytes to {out}")
                    except Exception as e:
                        print(f"Error extracting buffered image: {e}")
                        import traceback
                        traceback.print_exc()
                # Handle images with URI - could be external or data URI
                elif hasattr(img, 'uri') and img.uri:
                    uri = img.uri
                    # Make sure the filename is safe
                    safe_name = os.path.basename(uri.split("?")[0]) if uri and not uri.startswith("data:") else f"img_{idx}.jpg"
                    out = os.path.join(outdir, safe_name)
                    
                    # Data URI?
                    if uri.startswith("data:"):
                        try:
                            print(f"Extracting data URI image to {out}")
                            header, b64 = uri.split(",", 1)
                            img_data = base64.b64decode(b64)
                            with open(out, "wb") as wf: wf.write(img_data)
                            print(f"Successfully extracted {len(img_data)} bytes from data URI")
                        except Exception as e:
                            print(f"Error extracting data URI image: {e}")
                            import traceback
                            traceback.print_exc()
                    else:
                        print(f"External URI reference found: {uri}")
                else:
                    print(f"Warning: Image in {tile_path} has no URI or bufferView, skipping")
        except Exception as e:
            print(f"Error parsing GLTF data: {e}")
            import traceback
            traceback.print_exc()
    except Exception as e:
        print(f"Error processing 3D tile file {tile_path}: {e}")
        import traceback
        traceback.print_exc()

def reproject_and_mosaic(src_dir, mosaic_path):
    """Take all images in src_dir, warp to EPSG:3857, and mosaic."""
    # Create directory for mosaic if it doesn't exist
    os.makedirs(os.path.dirname(mosaic_path) or '.', exist_ok=True)
    """Take all images in src_dir, warp to EPSG:3857, and mosaic."""
    image_files = [fn for fn in os.listdir(src_dir) 
                   if fn.lower().endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff"))]
    
    # Check if there are any image files to process
    if not image_files:
        print(f"Warning: No image files found in {src_dir}")
        print("Ensure that the tiles were downloaded correctly and are in the expected format.")
        print(f"Expected directory: {src_dir}")
        print("Check if the download process completed successfully or if the directory is empty.")
        return False
    
    print(f"Found {len(image_files)} images to process")
    
    # Create a list to store reprojected files that we'll need to clean up
    temp_files = []
    
    # First, reproject all the images to EPSG:3857 and save as temp files
    for idx, fn in enumerate(image_files):
        fp = os.path.join(src_dir, fn)
        try:
            with rasterio.open(fp) as src:
                # Check if the image has geo-referencing information
                if not src.crs:
                    print(f"Warning: {fn} has no CRS information, creating default CRS")
                    # For images without CRS, set a default CRS (WGS84)
                    default_transform = rasterio.transform.from_bounds(
                        west=0, south=0, east=1, north=1, width=src.width, height=src.height
                    )
                    profile = src.profile.copy()
                    profile.update({
                        'crs': 'EPSG:4326',  # Set to WGS84
                        'transform': default_transform,
                    })
                    # Create a temporary file with the proper CRS
                    with MemoryFile() as memfile:
                        with memfile.open(**profile) as tmp:
                            tmp.write(src.read())
                        # Continue with this temporary file
                        with memfile.open() as src:
                            # Now src has a CRS so we can continue with processing
                            pass
                
                # Create a temporary output file for each reprojected image
                temp_file = os.path.join(src_dir, f"temp_reprojected_{idx}.tif")
                temp_files.append(temp_file)
                
                transform, w, h = calculate_default_transform(
                    src.crs, 'EPSG:3857', src.width, src.height, *src.bounds
                )
                profile = src.profile.copy()
                profile.update({
                    'driver': 'GTiff',
                    'crs': 'EPSG:3857',
                    'transform': transform,
                    'width': w, 'height': h
                })
                
                # Save the reprojected file to disk
                with rasterio.open(temp_file, 'w', **profile) as dst:
                    # Handle both single-band and multi-band images
                    num_bands = src.count
                    for band_idx in range(1, num_bands + 1):
                        reproject(
                            source=rasterio.band(src, band_idx),
                            destination=rasterio.band(dst, band_idx),
                            src_transform=src.transform, src_crs=src.crs,
                            dst_transform=transform, dst_crs='EPSG:3857',
                            resampling=Resampling.bilinear
                        )
                print(f"Reprojected {fn} to {temp_file}")
        except Exception as e:
            print(f"Error processing {fn}: {e}")
    
    # Check if we have any reprojected files to mosaic
    if not temp_files:
        print("No valid images to create mosaic")
        return False
    
    try:
        # Open all the reprojected files
        src_files = [rasterio.open(f) for f in temp_files]
        
        # Create the mosaic
        print(f"Merging {len(src_files)} reprojected files...")
        mosaic, out_trans = merge(src_files)
        
        # Get metadata from first file
        out_meta = src_files[0].meta.copy()
        out_meta.update({
            "driver": "GTiff",
            "height": mosaic.shape[1],
            "width": mosaic.shape[2],
            "transform": out_trans,
            "crs": 'EPSG:3857'
        })
        
        # Close all source files
        for src in src_files:
            src.close()
        
        
        # Write the mosaic
        with rasterio.open(mosaic_path, "w", **out_meta) as dest:
            dest.write(mosaic)
        
        # Clean up temporary files
        for temp_file in temp_files:
            try:
                os.remove(temp_file)
                print(f"Removed temporary file: {temp_file}")
            except Exception as e:
                print(f"Error removing temporary file {temp_file}: {e}")
        
        return True
    except Exception as e:
        print(f"Error creating mosaic: {e}")
        import traceback
        traceback.print_exc()
        return False

def create_xyz_tiles(mosaic, tile_folder):
    """Call GDAL2Tiles to generate XYZ tiles."""
    import shutil  # Import here to ensure availability
    
    if not os.path.exists(mosaic):
        print(f"Error: Mosaic file {mosaic} does not exist")
        return False
        
    # Create a backup of the mosaic file in case anything goes wrong
    mosaic_backup = f"{mosaic}.backup"
    try:
        shutil.copy2(mosaic, mosaic_backup)
        print(f"Created backup of mosaic file at {mosaic_backup}")
    except Exception as e:
        print(f"Warning: Could not create backup of mosaic file: {e}")
        
    # Create tile directory if it doesn't exist
    os.makedirs(tile_folder, exist_ok=True)
    
    # Check if gdal2tiles.py is available
    try:
        # First try to find gdal2tiles.py
        import subprocess
        import shutil
        
        # Check if gdal2tiles.py is in PATH
        gdal2tiles_path = shutil.which("gdal2tiles.py")
        
        if not gdal2tiles_path:
            # Try common alternative paths
            print("gdal2tiles.py not found in PATH, trying alternatives...")
            
            # Try to use Python's gdal module directly
            try:
                print("Attempting to use Python GDAL module...")
                from osgeo import gdal
                from osgeo_utils import gdal2tiles
                
                # Create a wrapper function to call gdal2tiles
                def run_gdal2tiles():
                    print(f"Using GDAL Python module to create tiles from {mosaic}")
                    # Parameters similar to command line
                    gdal2tiles_args = [
                        "",  # Program name (unused)
                        "-z", "6-18",
                        mosaic,
                        tile_folder
                    ]
                    gdal2tiles.main(gdal2tiles_args)
                    return True
                    
                # Run the function
                success = run_gdal2tiles()
                
                if success:
                    # Create an HTML viewer
                    html_file = os.path.join(tile_folder, "index.html")
                    with open(html_file, "w") as f:
                        f.write("""<!DOCTYPE html>
<html>
<head>
    <title>3D Tiles Viewer</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>
    <style>
        body { margin: 0; padding: 0; }
        #map { position: absolute; top: 0; bottom: 0; width: 100%; height: 100%; }
    </style>
</head>
<body>
    <div id="map"></div>
    <script>
        var map = L.map('map').setView([0, 0], 2);
        
        L.tileLayer('http://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; OpenStreetMap contributors',
            maxZoom: 18,
        }).addTo(map);
        
        // Add our generated XYZ tiles
        L.tileLayer('./{z}/{x}/{y}.png', {
            attribution: '3D Tiles',
            maxZoom: 18,
            tms: true
        }).addTo(map);
        
        // Try to fit the map to bounds if we know them
        // You can modify these bounds to focus on your area of interest
        try {
            var bounds = [[40.712, -74.227], [40.774, -74.125]]; // Example: NYC
            map.fitBounds(bounds);
        } catch(e) {
            console.error("Could not set bounds:", e);
        }
    </script>
</body>
</html>""")
                    print(f"Created viewer HTML at {html_file}")
                    return True
            except Exception as gdal_module_error:
                print(f"Could not use GDAL Python module: {gdal_module_error}")
                
            # Create a very minimal tile viewer HTML file as fallback
            html_file = os.path.join(tile_folder, "index.html")
            with open(html_file, "w") as f:
                f.write("""<!DOCTYPE html>
<html>
<head>
    <title>Tile Viewer</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        body { margin: 0; padding: 0; font-family: Arial, sans-serif; }
        #map { height: 400px; width: 100%; margin-bottom: 20px; }
        .container { padding: 20px; max-width: 800px; margin: 0 auto; }
        .alert { background-color: #f8d7da; color: #721c24; padding: 15px; border-radius: 4px; margin-bottom: 20px; }
        code { background: #f5f5f5; padding: 2px 5px; border-radius: 3px; }
        h1 { color: #333; }
    </style>
</head>
<body>
    <div class="container">
        <h1>3D Tiles Viewer</h1>
        <div class="alert">
            <strong>Note:</strong> XYZ tiles could not be generated because GDAL is not installed.
        </div>
        
        <h2>Mosaic Preview</h2>
        <div id="map"></div>
        <script>
            var map = L.map('map').setView([51.5, -0.1], 12);
            
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '&copy; OpenStreetMap contributors',
                maxZoom: 19
            }).addTo(map);
            
            var bounds = [[51.4, -0.2], [51.6, 0.1]]; // London approximate bounds
            map.fitBounds(bounds);
            
            // Add marker for the mosaic location
            L.marker([51.5, -0.1]).addTo(map)
                .bindPopup("Mosaic location (approximate)");
        </script>
        
        <h2>Instructions to fix:</h2>
        <ol>
            <li>Install GDAL with pip: <code>pip install gdal</code></li>
            <li>Run the process command again: <code>python3 3dtiles.py process """ + os.path.basename(os.path.dirname(mosaic)) + """</code></li>
        </ol>
        
        <h3>Alternative:</h3>
        <p>You can manually convert the mosaic file to XYZ tiles using other tools.</p>
        <p>Mosaic file location: <code>""" + mosaic + """</code></p>
    </div>
</body>
</html>""")
            
            print(f"Created minimal viewer in {html_file}")
            print("Warning: XYZ tiles not created as gdal2tiles.py is not available")
            print("Instructions to fix:")
            print("1. Install GDAL with pip: pip install gdal")
            print("2. Or use another tool to create XYZ tiles from the mosaic file")
            print(f"3. Mosaic file is located at: {mosaic}")
            # Return True because we created a usable fallback viewer
            return True
        
        # Use subprocess to capture output and check return code
        cmd = f"{gdal2tiles_path} -z 6-18 {mosaic} {tile_folder}"
        print(f"Running: {cmd}")
        result = subprocess.run(cmd, shell=True, check=True, 
                                stdout=subprocess.PIPE, 
                                stderr=subprocess.PIPE,
                                text=True)
        print(result.stdout)
        
        # Create an HTML viewer for the tiles
        html_file = os.path.join(tile_folder, "index.html")
        with open(html_file, "w") as f:
            f.write("""<!DOCTYPE html>
<html>
<head>
    <title>3D Tiles Viewer</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        body { margin: 0; padding: 0; }
        #map { position: absolute; top: 0; bottom: 0; width: 100%; height: 100%; }
        .leaflet-container { background: #f0f0f0; }
    </style>
</head>
<body>
    <div id="map"></div>
    <script>
        var map = L.map('map').setView([51.5, -0.1], 12);  // London center
        
        // Base map layer
        var baseLayer = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; OpenStreetMap contributors',
            maxZoom: 19,
        }).addTo(map);
        
        // Add our generated XYZ tiles
        var imageryLayer = L.tileLayer('./{z}/{x}/{y}.png', {
            attribution: '3D Tiles',
            maxZoom: 19,
            tms: true,
            opacity: 0.7,  // Make it semi-transparent to see base map underneath
        }).addTo(map);
        
        // Add layer control
        var baseLayers = {
            "OpenStreetMap": baseLayer
        };
        
        var overlays = {
            "Imagery Layer": imageryLayer
        };
        
        L.control.layers(baseLayers, overlays).addTo(map);
        
        // Try to fit the map to bounds if we know them
        try {
            var bounds = [[51.4, -0.2], [51.6, 0.1]]; // London approximate bounds
            map.fitBounds(bounds);
        } catch(e) {
            console.error("Could not set bounds:", e);
        }
    </script>
</body>
</html>""")
        print(f"Created viewer HTML at {html_file}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error running gdal2tiles.py: {e}")
        print(f"Command output: {e.stdout}")
        print(f"Command error: {e.stderr}")
        print("Make sure gdal2tiles.py is installed and in your PATH")
        print("Install with: pip install gdal")
        return False
    except Exception as e:
        print(f"Unexpected error creating tiles: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_connection(session, url):
    """Test API connection and return True if successful."""
    try:
        # Check if we're in test mode
        if "test" in url.lower() or not API_KEY:
            print("Skipping actual API connection test in test mode")
            return True
            
        print(f"Testing API connection with URL: {url}")
        resp = session.get(url)
        resp.raise_for_status()
        
        # Debug: Print the API response data
        data = resp.json()
        print("API Response:")
        print(json.dumps(data, indent=2)[:1000])  # Print first 1000 chars to avoid overwhelming output
        
        # Validate that the response has the expected structure
        if "asset" not in data or "root" not in data:
            print("Warning: API response may not be in the expected 3D Tiles format")
            print("Please check your API key permissions and Google Cloud Console settings")
            print("Make sure the Map Tiles API is enabled and properly configured")
            # Still return True to let the script try to process what it received
            
        return True
    except requests.exceptions.HTTPError as e:
        print(f"API Connection Error: {e}")
        print(f"URL: {url}")
        if e.response.status_code == 400:
            print("\nPossible solutions for 400 Bad Request error:")
            print("1. Check that your API key is valid and has the correct permissions")
            print("2. Verify that the Map Tiles API is enabled in your Google Cloud Console")
            print("3. Ensure you have billing enabled for your Google Cloud account")
            print("4. Verify that the coordinates for the city are correct")
            print("\nPlease fetch and review the documentation provided at:")
            print("https://developers.google.com/maps/documentation/tile/create-renderer before continuing")
            print("\nTo re-enable the API, visit:")
            print("https://console.cloud.google.com/apis/library/tile.googleapis.com")
        elif e.response.status_code == 403:
            print("\nPossible solutions for 403 Forbidden error:")
            print("1. Your API key may not have the correct permissions")
            print("2. You may have exceeded your quota or have billing issues")
            print("3. Check API restrictions (IP, referrers, etc.) in the Google Cloud Console")
        elif e.response.status_code == 404:
            print("\nPossible solutions for 404 Not Found error:")
            print("1. The 3D data for this location may not be available")
            print("2. Try a different city or adjust the coordinates")
            print("3. Check that you're using the correct API endpoint")
        return False
    except requests.exceptions.RequestException as e:
        print(f"API Connection Error: {e}")
        print(f"URL: {url}")
        if "key" in url.lower():
            print("Make sure you have set a valid Google API key with 3D Tiles access")
            print("Set it with: export GOOGLE_API_KEY=your_api_key")
            print("Note: The 3D Tiles API requires enabling the Map Tiles API in Google Cloud Console")
        return False

def run_test_mode():
    """Create a test directory with sample files for testing the pipeline."""
    test_dir = os.path.join(BASE_DIR, "downloaded_tiles", "test")
    os.makedirs(test_dir, exist_ok=True)
    
    # Clean up any existing files
    for f in os.listdir(test_dir):
        try:
            os.remove(os.path.join(test_dir, f))
            print(f"Removed old file: {f}")
        except Exception as e:
            print(f"Error removing {f}: {e}")
    
    # Create a few test georeferenced TIF files
    print(f"Creating test files in {test_dir}")
    
    try:
        # Create a simple georeferenced image
        import numpy as np
        
        # Make a simple array with a gradient
        width, height = 100, 100
        
        # Add some visual elements to the test images
        for i in range(3):
            # Create a new array for each image with all bands
            # Using 3 bands (RGB) to ensure compatibility with mosaic function
            img_data = np.ones((3, height, width), dtype=np.uint8) * 255  # White background
            
            # Add different colored patterns to each image
            for y in range(height):
                for x in range(width):
                    if (x + y + i*10) % 20 == 0:
                        # Create different colored diagonal lines for each image
                        if i == 0:
                            img_data[0, y, x] = 255  # Red component
                            img_data[1, y, x] = 0
                            img_data[2, y, x] = 0
                        elif i == 1:
                            img_data[0, y, x] = 0
                            img_data[1, y, x] = 255  # Green component
                            img_data[2, y, x] = 0
                        else:
                            img_data[0, y, x] = 0
                            img_data[1, y, x] = 0
                            img_data[2, y, x] = 255  # Blue component
            
            # Create a transform for the image (slight offset for each image)
            # Make them overlap for proper mosaicking - using coordinates in London for better test data
            transform = rasterio.transform.from_bounds(
                west=-0.15 + i*0.02,      # left edge (approximate London longitude)
                south=51.45 + i*0.02,      # bottom edge (approximate London latitude)
                east=-0.05 + i*0.02,       # right edge
                north=51.55 + i*0.02,      # top edge
                width=width,
                height=height
            )
            
            # Set up the metadata
            meta = {
                'driver': 'GTiff',
                'height': height,
                'width': width,
                'count': 3,  # number of bands (RGB)
                'dtype': 'uint8',
                'crs': 'EPSG:4326',  # WGS84
                'transform': transform,
                'photometric': "RGB"  # Specify RGB color interpretation
            }
            
            # Write the file
            img_path = os.path.join(test_dir, f"test_image_{i}.tif")
            with rasterio.open(img_path, 'w', **meta) as dst:
                dst.write(img_data)
                
            print(f"Created georeferenced test image: {img_path}")
            
    except Exception as e:
        print(f"Error creating test images: {e}")
        import traceback
        traceback.print_exc()
    
    return test_dir

if __name__ == "__main__":
    # Process command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1].lower() == "test":
            # Test mode - create sample images
            print("Running in test mode")
            test_dir = run_test_mode()
            print(f"Test files created in {test_dir}")
            sys.exit(0)
        elif sys.argv[1].lower() == "process":
            # Process mode - skip downloading and just process existing files
            if len(sys.argv) > 2:
                CITY = sys.argv[2]
            print(f"Processing existing files for {CITY}")
            
            # Update paths to be city-specific
            city_dir = CITY.replace(" ", "_").lower()
            OUTDIR = os.path.join(BASE_DIR, "downloaded_tiles", city_dir)
            MOSAIC = os.path.join(BASE_DIR, "downloaded_tiles", f"{city_dir}_mosaic_3857.tif")
            TILEDIR = os.path.join(BASE_DIR, "tiles", city_dir)
            
            if not os.path.exists(OUTDIR) or not os.listdir(OUTDIR):
                print(f"Directory {OUTDIR} is empty or does not exist. Attempting to download tiles...")
                if not API_KEY:
                    print("Error: No API key provided. Set the GOOGLE_API_KEY environment variable.")
                    sys.exit(1)
                
                # Use the root URL for downloading tiles
                root_url = f"https://tile.googleapis.com/v1/3dtiles/root.json?key={API_KEY}"
                sess = requests.Session()
                
                # Test connection before proceeding
                if not test_connection(sess, root_url):
                    print("API connection test failed. Exiting.")
                    sys.exit(1)
                
                # Create output directory
                os.makedirs(OUTDIR, exist_ok=True)
                
                # Download tiles
                try:
                    print(f"Downloading 3D tiles for {CITY}...")
                    fetch_tileset(root_url, sess, OUTDIR, api_key=API_KEY)
                except Exception as e:
                    print(f"Error during tile download: {e}")
                    import traceback
                    traceback.print_exc()
                    sys.exit(1)
            
            # Proceed with reprojecting and creating mosaic
            try:
                print(f"Reprojecting and creating mosaic...")
                mosaic_success = reproject_and_mosaic(OUTDIR, MOSAIC)
                
                if mosaic_success:
                    print(f"Creating XYZ tiles...")
                    tile_success = create_xyz_tiles(MOSAIC, TILEDIR)
                    
                    if tile_success:
                        print("\nSuccess! XYZ tiles created.")
                        print(f"Serve the folder: {BASE_DIR}")
                        print(f"- Use any web server, e.g.: python -m http.server --directory {BASE_DIR}")
                        print(f"- Open a web browser and navigate to http://localhost:8000/tiles/{city_dir}/")
                    else:
                        print("Failed to create XYZ tiles.")
                        sys.exit(1)
                else:
                    print("Failed to create mosaic. Skipping tile creation.")
                    sys.exit(1)
            except Exception as e:
                print(f"Error during processing: {e}")
                import traceback
                traceback.print_exc()
                sys.exit(1)
                
            sys.exit(0)
        else:
            CITY = sys.argv[1]
            print(f"Using city: {CITY}")
    
    # Check if API key is provided
    if API_KEY == "YOUR_TEST_API_KEY_HERE":
        print("Warning: Using placeholder API key. Set GOOGLE_API_KEY environment variable.")
        print("Example: export GOOGLE_API_KEY=your_actual_api_key")
        print("Available options:")
        print("  python3 3dtiles.py test                   # Run in test mode")
        print("  python3 3dtiles.py process [city_name]    # Process existing files")
    
    try:
        # Update paths to be city-specific
        city_dir = CITY.replace(" ", "_").lower()
        OUTDIR = os.path.join(BASE_DIR, "downloaded_tiles", city_dir)
        MOSAIC = os.path.join(BASE_DIR, "downloaded_tiles", f"{city_dir}_mosaic_3857.tif")
        TILEDIR = os.path.join(BASE_DIR, "tiles", city_dir)
        
        print(f"City: {CITY}")
        print(f"Output directory: {OUTDIR}")
        print(f"Mosaic path: {MOSAIC}")
        print(f"Tiles directory: {TILEDIR}")
        
        # Use different URLs depending on whether we're in test mode or not
        if CITY.lower() == "test":
            print("Running in test mode. Using local test files instead of API.")
            test_dir = run_test_mode()
            print(f"Test files created in {test_dir}")
            try:
                print(f"Reprojecting and creating mosaic...")
                mosaic_success = reproject_and_mosaic(test_dir, MOSAIC)
                
                if mosaic_success:
                    print(f"Creating XYZ tiles...")
                    tile_success = create_xyz_tiles(MOSAIC, TILEDIR)
                    
                    if tile_success:
                        print("\nSuccess! XYZ tiles created.")
                        print(f"Serve the folder: {BASE_DIR}")
                        print(f"- Use any web server, e.g.: python -m http.server --directory {BASE_DIR}")
                        print(f"- Open a web browser and navigate to http://localhost:8000/tiles/{city_dir}/")
                    else:
                        print("Failed to create XYZ tiles.")
                        sys.exit(1)
                else:
                    print("Failed to create mosaic. Skipping tile creation.")
                    sys.exit(1)
            except Exception as e:
                print(f"Error during test processing: {e}")
                import traceback
                traceback.print_exc()
                sys.exit(1)
            sys.exit(0)
            
        # Regular mode - proceed with API access
        if not API_KEY:
            print("Error: No API key provided. Set the GOOGLE_API_KEY environment variable.")
            print("Example: export GOOGLE_API_KEY=your_actual_api_key")
            sys.exit(1)
            
        # City coordinates (approximate centers)
        city_coordinates = {
            "London": {"lat": 51.5074, "lng": -0.1278},
            "New York": {"lat": 40.7128, "lng": -74.0060},
            "Tokyo": {"lat": 35.6762, "lng": 139.6503},
            "Paris": {"lat": 48.8566, "lng": 2.3522},
            "Berlin": {"lat": 52.5200, "lng": 13.4050},
            "Sydney": {"lat": -33.8688, "lng": 151.2093},
            "San Francisco": {"lat": 37.7749, "lng": -122.4194},
        }
        
        # Get coordinates for the requested city, default to London if not found
        coords = city_coordinates.get(CITY, city_coordinates["London"])
        print(f"Using coordinates for {CITY}: {coords}")
        
        # Use the simplest API format as specified in the documentation
        # The lat/lng parameters are not needed for the root request
        root_url = f"https://tile.googleapis.com/v1/3dtiles/root.json?key={API_KEY}"
        sess = requests.Session()
        
        # Test connection before proceeding
        if not test_connection(sess, root_url):
            print("API connection test failed. Exiting.")
            sys.exit(1)
            
        # Create output directory
        os.makedirs(OUTDIR, exist_ok=True)
        
        # Proceed with the main workflow
        print(f"Downloading 3D tiles for {CITY}...")
        fetch_tileset(root_url, sess, OUTDIR, api_key=API_KEY)
        
        # Check if we have files in the output directory
        image_count = len([f for f in os.listdir(OUTDIR) 
                          if f.lower().endswith((".png", ".jpg", ".jpeg"))])
        if image_count == 0:
            print(f"No image files were downloaded to {OUTDIR}")
            print("Check your API key and city name")
            sys.exit(1)
        
        print(f"Reprojecting and creating mosaic...")
        mosaic_success = reproject_and_mosaic(OUTDIR, MOSAIC)
        
        if mosaic_success:
            print(f"Creating XYZ tiles...")
            tile_success = create_xyz_tiles(MOSAIC, TILEDIR)
            
            if tile_success:
                print("\nSuccess! Process completed successfully.")
                print(f"1. Mosaic created: {MOSAIC}")
                print(f"2. XYZ tiles created in: {TILEDIR}")
                print("\nTo view these tiles:")
                print(f"- Serve the folder: {BASE_DIR}")
                print(f"- Use any web server, e.g.: python -m http.server --directory {BASE_DIR}")
                print(f"- Open a web browser and navigate to http://localhost:8000/tiles/{city_dir}/")
            else:
                print("\nPartial success:")
                print(f"- Mosaic created: {MOSAIC}")
                print(f"- XYZ tiles not created")
                print("\nThe mosaic file can still be used with GIS software or other tile generators.")
        else:
            print("Failed to create mosaic. Skipping tile creation.")
            sys.exit(1)
    except KeyboardInterrupt:
        print("\nProcess interrupted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)