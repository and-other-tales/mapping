#!/usr/bin/env python3
"""
3D Tiles Downloader

This script downloads 3D tiles for a specified city using Google's 3D Tiles API,
extracts textures, creates a mosaic, and generates XYZ tiles.

Usage:
    python3 3dtiles.py <city_name>              # Download and process tiles for the specified city
    python3 3dtiles.py test                     # Run in test mode without API key
    python3 3dtiles.py process [city_name]      # Process existing downloaded tiles without API access

Required environment variable (for download mode):
    GOOGLE_API_KEY - Your Google API key with 3D Tiles access

Output directories:
    downloaded_tiles/<city>/ - Raw downloaded image files
    tiles/<city>/ - Processed XYZ tiles for web mapping
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
API_KEY = os.getenv("GOOGLE_API_KEY", "YOUR_TEST_API_KEY_HERE")
CITY    = "London"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTDIR  = "downloaded_tiles"  # Will be updated with city-specific path
MOSAIC  = "mosaic_3857.tif"   # Will be updated with city-specific path
TILEDIR = "tiles"             # Will be updated with city-specific path

def fetch_tileset(url, session, outdir):
    """Recursively download all tiles and extract textures."""
    os.makedirs(outdir, exist_ok=True)
    resp = session.get(url); resp.raise_for_status()
    data = resp.json()
    # Download this tile’s content
    if "content" in data:
        uri = data["content"]["uri"]
        r = session.get(uri, stream=True); r.raise_for_status()
        fpath = os.path.join(outdir, os.path.basename(uri))
        with open(fpath, "wb") as f: shutil.copyfileobj(r.raw, f)
        extract_textures(fpath, outdir)
    # Recurse into children
    for child in data.get("children", []):
        # children URIs are relative to this JSON location
        child_url = os.path.join(os.path.dirname(url), child["content"]["uri"])
        fetch_tileset(child_url, session, outdir)

def extract_textures(tile_path, outdir):
    """Pull out all images in the GLTF chunk of a .b3dm/.glb."""
    # strip off the 28-byte B3DM header if needed
    with open(tile_path, "rb") as f:
        magic = f.read(4)
        f.seek(0)
        if magic == b"b3dm":
            f.seek(28)  # skip header
        glb_data = f.read()
    # Load GLTF in-memory
    gltf = GLTF2.load_from_bytes(glb_data)
    for img in gltf.images:
        # Some GLTFs might not have URI, handle this case
        if not hasattr(img, 'uri'):
            print(f"Warning: Image in {tile_path} has no URI, skipping")
            continue
        
        uri = img.uri
        # Make sure the filename is safe
        safe_name = os.path.basename(uri.split("?")[0]) if uri and not uri.startswith("data:") else f"img_{id(img)}.jpg"
        out = os.path.join(outdir, safe_name)
        
        # data URI?
        if uri and uri.startswith("data:"):
            try:
                header, b64 = uri.split(",", 1)
                img_data = base64.b64decode(b64)
                with open(out, "wb") as wf: wf.write(img_data)
            except Exception as e:
                print(f"Error extracting data URI image: {e}")
        elif hasattr(img, 'bufferView') and img.bufferView is not None:
            try:
                # Get buffer view
                buffer_view = gltf.bufferViews[img.bufferView]
                # Find which buffer this view references
                buffer_index = buffer_view.buffer
                # Get byte offset and length
                byte_offset = buffer_view.byteOffset
                byte_length = buffer_view.byteLength
                
                # Extract bytes from the correct buffer
                img_bytes = glb_data[byte_offset:byte_offset + byte_length]
                with open(out, "wb") as wf: wf.write(img_bytes)
            except Exception as e:
                print(f"Error extracting buffered image: {e}")

def reproject_and_mosaic(src_dir, mosaic_path):
    """Take all images in src_dir, warp to EPSG:3857, and mosaic."""
    image_files = [fn for fn in os.listdir(src_dir) 
                   if fn.lower().endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff"))]
    
    # Check if there are any image files to process
    if not image_files:
        print(f"Warning: No image files found in {src_dir}")
        print("No mosaic will be created.")
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
                    print(f"Warning: {fn} has no CRS information, skipping")
                    continue
                
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
        
        # Create directory for mosaic if it doesn't exist
        os.makedirs(os.path.dirname(mosaic_path) or '.', exist_ok=True)
        
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
    if not os.path.exists(mosaic):
        print(f"Error: Mosaic file {mosaic} does not exist")
        return False
        
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
            # Try common alternative paths or use Python module
            print("gdal2tiles.py not found in PATH, trying alternatives...")
            
            # Create a very minimal tile viewer HTML file
            html_file = os.path.join(tile_folder, "index.html")
            with open(html_file, "w") as f:
                f.write("""<!DOCTYPE html>
<html>
<head>
    <title>Tile Viewer</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { margin: 0; padding: 0; }
        #map { position: absolute; top: 0; bottom: 0; width: 100%; }
    </style>
</head>
<body>
    <div id="map"></div>
    <p>GDAL2Tiles not available. Install with: pip install gdal</p>
    <p>Or manually convert the mosaic file to XYZ tiles.</p>
    <p>Mosaic file: """ + mosaic + """</p>
</body>
</html>""")
            
            print(f"Created minimal viewer in {html_file}")
            print("Warning: XYZ tiles not created as gdal2tiles.py is not available")
            print("Instructions to fix:")
            print("1. Install GDAL with pip: pip install gdal")
            print("2. Or use another tool to create XYZ tiles from the mosaic file")
            print(f"3. Mosaic file is located at: {mosaic}")
            return False
        
        # Use subprocess to capture output and check return code
        cmd = f"{gdal2tiles_path} -z 6-18 {mosaic} {tile_folder}"
        print(f"Running: {cmd}")
        result = subprocess.run(cmd, shell=True, check=True, 
                                stdout=subprocess.PIPE, 
                                stderr=subprocess.PIPE,
                                text=True)
        print(result.stdout)
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
        resp = session.get(url)
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"API Connection Error: {e}")
        print(f"URL: {url}")
        if "key" in url.lower():
            print("Make sure you have set a valid Google API key with 3D Tiles access")
            print("Set it with: export GOOGLE_API_KEY=your_api_key")
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
            # Make them overlap for proper mosaicking
            transform = rasterio.transform.from_bounds(
                west=100 + i*0.05,       # left edge
                south=50 + i*0.05,        # bottom edge
                east=100.1 + i*0.05,      # right edge
                north=50.1 + i*0.05,      # top edge
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
            
            if not os.path.exists(OUTDIR):
                print(f"Error: Directory {OUTDIR} does not exist.")
                print(f"Please download tiles first or create the directory.")
                sys.exit(1)
                
            try:
                print(f"Reprojecting and creating mosaic...")
                mosaic_success = reproject_and_mosaic(OUTDIR, MOSAIC)
                
                if mosaic_success:
                    print(f"Creating XYZ tiles...")
                    tile_success = create_xyz_tiles(MOSAIC, TILEDIR)
                    
                    if tile_success:
                        print("\nSuccess! XYZ tiles created.")
                        print(f"Serve the folder: {TILEDIR}")
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
        
        root_url = f"https://tile.googleapis.com/v1/3dtiles/root.json?city={CITY.replace(' ','%20')}&key={API_KEY}"
        sess = requests.Session()
        
        # Test connection before proceeding
        if not test_connection(sess, root_url):
            print("API connection test failed. Exiting.")
            sys.exit(1)
            
        # Create output directory
        os.makedirs(OUTDIR, exist_ok=True)
        
        # Proceed with the main workflow
        print(f"Downloading 3D tiles for {CITY}...")
        fetch_tileset(root_url, sess, OUTDIR)
        
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
                print(f"- Serve the folder: {TILEDIR}")
                print(f"- Use any web server, e.g.: python -m http.server --directory {TILEDIR}")
                print("- Open a web browser and navigate to the served URL")
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
