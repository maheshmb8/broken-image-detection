"""
This repository demonstrates engineering patterns and image-quality
analysis techniques and is not intended to run out-of-the-box.
"""

# Data manipulation and analysis
import pandas as pd
import numpy as np

# Time and date utilities
import time
from datetime import date, timedelta, datetime

# Excel file handling
import xlrd

# Randomization utilities (used for delays / sampling)
import random

# Operating system and file handling
import os
import re
import sys
import pickle

# HTTP requests and networking
import requests
from io import BytesIO
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Image processing and analysis
from PIL import Image, ImageTk
import cv2
import imagehash

# Concurrency for parallel image processing
from concurrent.futures import ThreadPoolExecutor, as_completed

# Database connectivity (data warehouse integration)
from snowflake.sqlalchemy import URL
from sqlalchemy import create_engine
import snowflake.connector

# Combinatorics for duplicate image comparison
from itertools import combinations

# Google Sheets integration (configuration / logging)
import gspread
from google.oauth2.service_account import Credentials
from oauth2client.service_account import ServiceAccountCredentials

# GUI framework for desktop application
import tkinter as tk
from tkinter import messagebox, ttk

# Exception handling and debugging
import traceback

# Warning suppression for cleaner logs
import warnings
warnings.filterwarnings("ignore")


def global_exception_handler(exc_type, exc_value, exc_traceback):
    # Suppress full traceback and show a friendly message
    messagebox.showerror("Unexpected Error", "Something went wrong. Please try again.")
    # You could also log it silently if needed

sys.excepthook = global_exception_handler
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

# Google scraping json
creds_path = r"your_google_api_creds.json"

creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
client = gspread.authorize(creds)
spreadsheet = client.open("your sheet file")
worksheet = spreadsheet.worksheet("data with controllable variables")
data = worksheet.get_all_values()
variables_gsheet_df = pd.DataFrame(data[1:], columns=data[0])
current_version = int(variables_gsheet_df.iloc[:,0][1])
acceptable_users = os.getenv("ALLOWED_USERS")

headers = {
    "User-Agent": os.getenv("IMAGE_USER_AGENT"),
    "x-akamai-secret": os.getenv("IMAGE_ACCESS_TOKEN")
}

custom_config = "--psm 6"
colors_top = 10
structure_detail = 38
image_detail = 7
# ------------------------------------------------------------------
# Configuration thresholds (loaded from environment variables)
# ------------------------------------------------------------------

savepoint = int(os.getenv("SAVEPOINT", 0))

PHASH_THRESH = float(os.getenv("PHASH_THRESH", 0.90))
COLOR_THRESH = float(os.getenv("COLOR_THRESH", 0.90))
WHITE_THRESH = float(os.getenv("WHITE_THRESH", 0.95))

SAMPLE_DATA_SIZE = int(os.getenv("SAMPLE_DATA_SIZE", 0))
LAPLACIAN_THRESH = int(os.getenv("LAPLACIAN_THRESH", 100))

delays = (
    [0.1]*10 +
    [0.2]*6 +
    [0.3]*10 +
    [0.5]*10 +
    [1]*8 +
    [2, 3, 5]
)
bkimage_workers = 8
session = requests.Session()
retry_strategy = Retry(
    total=15,                      # Max retries
    backoff_factor=3,             # Exponential backoff (1s, 2s, 4s...)
    status_forcelist=[500, 502, 503, 504, 505, 507, 508, 510, 511, 408, 429, 521, 522, 524],  # Comprehensive list of retryable errors
    allowed_methods=["GET"],        # Retry only for GET requests
    backoff_max = 180
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("https://", adapter)
timeout_tm = 60
bk_images_check = []


# ------------------------------------------------------------------
# Anonymized SQL queries
# These queries illustrate how product image data is enriched
# with catalog and department metadata from a data warehouse.
# Actual schemas, databases, and table names are excluded.
# ------------------------------------------------------------------

# Fetch active products with valid product IDs (live data use case)
test_q = """
select img.*
from PROD_DB.ODS_PRODUCT_IMAGE_URL img
inner join PROD_DB.DIM_PRODUCT_CATALOG cat
  on img.PRODUCT_ID = cat.PRODUCT_ID
where length(cat.PRODUCT_ID) = 13
  and cat.IS_ACTIVE = true
"""

# Fetch all product image records (no filtering)
test_q1 = """
select img.*
from PROD_DB.ODS_PRODUCT_IMAGE_URL img
"""

# Fetch department-level metadata for products
dept_qry = """
select
  PRODUCT_ID as product_id,
  mode(DEPARTMENT_ID) as department_id,
  mode(DEPARTMENT_NAME) as department_name
from PROD_DB.FACT_PRODUCT_DAILY
group by PRODUCT_ID
"""

# Fetch additional product metadata used for similarity grouping
test_q2 = """
select
  PRODUCT_ID as product_id,
  mode(GROUP_ID) as group_id,
  mode(DEPARTMENT_ID) as department_id,
  mode(DEPARTMENT_NAME) as department_name,
  mode(MANUFACTURER_ID) as manufacturer_id,
  mode(MANUFACTURER_NAME) as manufacturer_name,
  mode(CATEGORY) as category
from PROD_DB.FACT_PRODUCT_DAILY
group by PRODUCT_ID
"""

# ------------------------------------------------------------------
# Warehouse / role placeholders (not executed in public version)
# ------------------------------------------------------------------

# Placeholder for role selection in the data warehouse
sya_role_q = "-- set appropriate read-only role"

# Placeholder for compute warehouse selection
sya_wh_q = "-- set appropriate compute warehouse"

# Placeholder for database selection
sya_db_q = "-- set appropriate database"

# Timestamp used for output versioning and file naming
list_save_var = datetime.today().strftime("%Y_%b_%d_%H%M%S")



# In[ ]:


def update_progress_file(done, total):
    # Delete any previous *_done.txt file
    for file in os.listdir():
        if file.endswith("done.txt"):
            os.remove(file)
    filename = f"{done}_{total}done.txt"
    open(filename, "w").close()

def cleanup_pickles():
    try:
        files = [f for f in os.listdir() if f.endswith(".pkl") and "partial_output" in f.lower()]
        for f in files:
            os.remove(f)
    except:
        pass

def is_mostly_white(x, percent_threshold=WHITE_THRESH, acceptable_colors=['255', '255, 255, 255']):
    try:
        perc_str = x.split(',')[0][1:]
        color_str = x.split(',', 1)[1].strip()
        perc = float(perc_str)
        return perc > percent_threshold and any(val in color_str for val in acceptable_colors)
    except Exception:
        return False

def update_logs(row_data):
    # Setup connection
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_path = r"your_google_api_creds.json"
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    client = gspread.authorize(creds)
    spreadsheet = client.open("your sheet file")
    worksheet = spreadsheet.worksheet("data with controllable variables")
    worksheet.append_row(row_data, value_input_option='RAW')

def fetch_bkimage(base_url, inx, product_ID):
    """
    Fetch and analyze a single image URL to detect broken or low-quality images.

    Returns a list of image quality metrics and a final status flag.
    """
    try:
        # Download image with retries, redirects allowed, SSL verification disabled
        response = session.get(
            base_url,
            timeout=timeout_tm,
            allow_redirects=True,
            verify=False,
            headers=headers
        )

        # Random delay to avoid hammering the source server
        sleep_time = random.choice(delays)
        time.sleep(sleep_time)

        # Compute image size in KB
        image_size_kb = round(len(response.content) / 1024, 2)

        try:
            # Attempt to load image into PIL
            image = Image.open(BytesIO(response.content))
        except:
            # Image bytes could not be decoded
            return [
                base_url, inx, product_ID,
                None, None, image_size_kb,
                None, None, None,
                None, None, None,
                None, 'UNREADABLE IMAGE'
            ]

        # Extract color distribution from the image
        colors = image.getcolors(maxcolors=1000000)
        total_pixels = image.size[0] * image.size[1]

        # Image dimensions and aspect ratio
        width, height = image.size
        aspect_ratio = round(width / height, 2)

        # Compute perceptual hash for duplicate detection
        phash_avg = imagehash.average_hash(
            image, hash_size=structure_detail
        )

        # Compute blur score using Laplacian variance
        laplacian_var = round(
            cv2.Laplacian(
                np.array(image.convert('L')),
                cv2.CV_64F
            ).var(),
            2
        )

        # Resize image and compute color histogram for similarity checks
        img1 = image.resize((128, 128)).convert('RGB')
        arr1 = np.array(img1)
        hist1 = cv2.calcHist(
            [arr1],
            [0, 1, 2],
            None,
            [8, 8, 8],
            [0, 256, 0, 256, 0, 256]
        )
        color_hash = cv2.normalize(hist1, hist1).flatten()

        # Calculate color dominance percentages
        color_percentages = [
            (round(count / total_pixels * 100, 2), color)
            for count, color in colors
        ]
        color_percentages.sort(reverse=True, key=lambda x: x[0])

        # Store dominant and top-N color summaries
        mainper = str(color_percentages[0])
        t5cols_str = str(color_percentages[:colors_top])

        # Detect grey placeholder images (dominant light-grey color)
        grey_flag = (
            ((color_percentages[0][0] > 91) &
             (color_percentages[0][1] == 246)) |
            ((color_percentages[0][0] > 91) &
             (color_percentages[0][1] == (246, 246, 246)))
        )

        # Close image to free memory
        image.close()

        # Return metrics and final status
        if not grey_flag:
            return [
                base_url, inx, product_ID,
                t5cols_str, mainper, image_size_kb,
                total_pixels, width, height, aspect_ratio,
                phash_avg, color_hash, laplacian_var,
                'NO ISSUES'
            ]
        else:
            return [
                base_url, inx, product_ID,
                t5cols_str, mainper, image_size_kb,
                total_pixels, width, height, aspect_ratio,
                phash_avg, color_hash, laplacian_var,
                'BROKEN IMAGE'
            ]

    except Exception:
        # Catch-all for network, decoding, or processing failures
        return [
            None, None, None,
            None, None, None,
            None, None, None,
            None, None, None,
            None, 'CODE ERROR'
        ]



def main3(pickle_save,userB,password, save_every=savepoint):
    global conn,image_df,bk_images_check,list_save_var
    warehouse = "-- set appropriate compute warehouse"
    role = "-- set appropriate read-only role"
    account = 'input your sql engine account'
    schema = "-- set appropriate schema"
    database = "-- set appropriate database"
    try:
        conn = sql_connector(
                        role=role,
                        user=userB,
                        password=password,
                        account=account,
                        database=database,
                        warehouse=warehouse,
                        schema= schema
                        )
    except Exception:
        messagebox.showerror("Connection Error", "Could not connect to Snowflake.")
        return

    conn.cursor().execute(sya_role_q)
    conn.commit()
    conn.cursor().execute(sya_wh_q)
    conn.commit()
    conn.cursor().execute(sya_db_q)
    conn.commit()
    if mode_var.get() == 'Live':
        image_df = pd.read_sql(test_q,conn)
    else:
        image_df = pd.read_sql(test_q1,conn)
    image_df['PRODUCT_CODE2'] = image_df['DW_MPID']+';'
    image_df = image_df[image_df['PRODUCT_CODE2'].str.len()>10]
    image_df = image_df[(image_df['IMAGE_URL_FINAL'].fillna('NA').str.len()>5)]
    image_df.reset_index(inplace=True)
    image_df = image_df[['BASE_URL', 'PRODUCT_CODE2','DW_MPID', 'DW_COLOR', 'DW_COLOR_SWATCHABLE','DW_COLOR_SELECTABLE', 'DW_CTAG', 'DW_CNAME','DW_PDP_URL', 'DW_IS_AVAIL','DW_IS_ONLINE', 'DW_COLOR_URL_FINAL', 'IMAGE_URL_FINAL']].reset_index()
    image_df.rename(columns={'index': 'O_IDX'}, inplace=True)
    image_df['IMAGE_URL_FINAL'] = image_df['IMAGE_URL_FINAL'].str.replace('http:','https:')
    if SAMPLE_DATA_SIZE > 1:
    	data_filter = min(SAMPLE_DATA_SIZE,image_df.shape[0])
    	image_df = image_df.head(data_filter) ############### CHANGEHEADER@111
    else:
    	pass

    output_path = f'BKimage_df_{pickle_save}.pkl'
    # output_path['PHASH'] = output_path['PHASH'].astype(str)
    # output_path.to_parquet(f"BKimage_df_parq_{pickle_save}.parquet", index=False)

    # Load existing progress if available
    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        BKimage_df_existing = pd.read_pickle(output_path)  
        done_oidx = set(BKimage_df_existing['O_IDX'])  
    else:  
        BKimage_df_existing = pd.DataFrame()  
        done_oidx = set()  

    image_df = image_df[~image_df['O_IDX'].isin(done_oidx)]  

    if image_df.empty:  
        return BKimage_df_existing  

    indices = image_df['O_IDX'].values  
    product_IDs = image_df['PRODUCT_CODE2'].values  
    image_urls = image_df['IMAGE_URL_FINAL'].values  

    columns = ['IMAGE_URL_FINAL','O_IDX','product_ID2','IMAGE_RGB_COLORS','TOP_COLOR','IMAGE_SIZE',
               'TOTAL_PIXELS','WIDTH','HEIGHT','ASPECT_RATIO','PHASH','COLOR_HASH','LAPLACIAN_VAR','STATE']
    bk_images_check = []  

    try:
        with ThreadPoolExecutor(max_workers=bkimage_workers) as executor:  
            futures = {executor.submit(fetch_bkimage, url, idx, pid): idx  # UPDATED: using futures dictionary
                       for url, idx, pid in zip(image_urls, indices, product_IDs)}  # UPDATED

            for i, future in enumerate(as_completed(futures)):
                try:
                    result = future.result()  # UPDATED
                    if result:  
                        bk_images_check.append(result)  # UPDATED

                    if len(bk_images_check) >= save_every:  
                        temp_df = pd.DataFrame(bk_images_check, columns=columns)  
                        BKimage_df_existing = pd.concat([BKimage_df_existing, temp_df], ignore_index=True)  
                        BKimage_df_existing.to_pickle(output_path)  
                        update_progress_file(len(BKimage_df_existing), len(futures))
                        bk_images_check = []  

                except Exception as e:  
                    pass

    except KeyboardInterrupt:  
        executor.shutdown(wait=False)  
        raise  
    if bk_images_check:  
        temp_df = pd.DataFrame(bk_images_check, columns=columns)  
        BKimage_df_existing = pd.concat([BKimage_df_existing, temp_df], ignore_index=True)  
        BKimage_df_existing.to_pickle(f'Final_BKimage_df_{list_save_var}.pkl')  
    BKimage_df_existing.to_pickle(f'Final_BKimage_df_{list_save_var}.pkl')

    return BKimage_df_existing  # UPDATED: returns full saved DF

def output():
    """
    Post-processing and reporting function.

    - Loads processed image analysis results
    - Enriches with department and product metadata
    - Identifies broken, duplicate, blurry, white, swatch, and odd-size images
    - Exports consolidated results to an Excel report
    - Logs execution summary
    """

    # Declare globals populated by this function
    global BKimage_df, final_df, broken_images_final, swatch_issue_df
    global bk_summary, main_df, matches_df, matches_df_sorted

    # Load previously saved image analysis results
    with open(f'Final_BKimage_df_{list_save_var}.pkl', "rb") as f:
        BKimage_df = pickle.load(f)

    # Drop high-cardinality RGB color column (not needed downstream)
    BKimage_df.drop('IMAGE_RGB_COLORS', axis=1, inplace=True)

    # Load department-level metadata
    dept_names = pd.read_sql(dept_qry, conn)

    # Merge image analysis results with original image dataset
    final_df = image_df.merge(
        BKimage_df,
        how='left',
        left_on=['PRODUCT_CODE2', 'O_IDX', 'IMAGE_URL_FINAL'],
        right_on=['product_ID2', 'O_IDX', 'IMAGE_URL_FINAL']
    )

    # Enrich with department details
    final_df = final_df.merge(
        dept_names,
        how='left',
        left_on='product_ID2',
        right_on='SVS2'
    )

    # Identify swatch issues (same image appearing multiple times per URL)
    final_df['SWATCH_ISSUE'] = (
        final_df.groupby('IMAGE_URL_FINAL')['O_IDX'].transform('size') > 1
    )

    # Identify mostly-white images
    mostly_white = final_df[
        final_df['TOP_COLOR'].apply(lambda x: is_mostly_white(x))
    ][[
        'O_IDX', 'product_ID2', 'DEPARTMENT_ID', 'DEPARTMENT_NAME',
        'DW_PDP_URL', 'DW_COLOR_SWATCHABLE', 'DW_COLOR_SELECTABLE',
        'DW_IS_AVAIL', 'DW_IS_ONLINE', 'DW_COLOR', 'DW_CTAG',
        'PHASH', 'COLOR_HASH', 'IMAGE_URL_FINAL',
        'TOP_COLOR', 'STATE', 'SWATCH_ISSUE'
    ]].reset_index(drop=True)

    # Extract broken images
    broken_images_final = final_df[
        final_df['STATE'] == 'BROKEN IMAGE'
    ][[
        'O_IDX', 'product_ID2', 'DEPARTMENT_ID', 'DEPARTMENT_NAME',
        'DW_PDP_URL', 'DW_COLOR_SWATCHABLE', 'DW_COLOR_SELECTABLE',
        'DW_IS_AVAIL', 'DW_IS_ONLINE', 'DW_COLOR', 'DW_CTAG',
        'PHASH', 'COLOR_HASH', 'IMAGE_URL_FINAL',
        'TOP_COLOR', 'STATE', 'SWATCH_ISSUE'
    ]].reset_index(drop=True)

    # Extract swatch-related image issues
    swatch_issue_df = final_df[
        final_df['SWATCH_ISSUE'] == True
    ][[
        'O_IDX', 'product_ID2', 'DEPARTMENT_ID', 'DEPARTMENT_NAME',
        'DW_PDP_URL', 'DW_COLOR_SWATCHABLE', 'DW_COLOR_SELECTABLE',
        'DW_IS_AVAIL', 'DW_IS_ONLINE', 'DW_COLOR', 'DW_CTAG',
        'PHASH', 'COLOR_HASH', 'IMAGE_URL_FINAL',
        'TOP_COLOR', 'STATE', 'SWATCH_ISSUE'
    ]].reset_index(drop=True)

    # Identify images with non-standard dimensions
    odd_sizes = final_df[
        (
            final_df['WIDTH'].fillna(0).astype(int).astype(str).str.strip() +
            '|' +
            final_df['HEIGHT'].fillna(0).astype(int).astype(str).str.strip()
        ) != '300|400'
    ][[
        'O_IDX', 'product_ID2', 'DEPARTMENT_ID', 'DEPARTMENT_NAME',
        'DW_PDP_URL', 'IMAGE_URL_FINAL',
        'TOTAL_PIXELS', 'WIDTH', 'HEIGHT', 'ASPECT_RATIO'
    ]]

    # Identify blurry images using Laplacian variance threshold
    blurry_images = final_df[
        final_df['LAPLACIAN_VAR'] <= LAPLACIAN_THRESH
    ][[
        'O_IDX', 'product_ID2', 'DEPARTMENT_ID', 'DEPARTMENT_NAME',
        'DW_PDP_URL', 'IMAGE_URL_FINAL', 'LAPLACIAN_VAR'
    ]]

    # Load additional product metadata for duplicate detection
    chqdf = pd.read_sql(test_q2, conn)

    # Merge metadata with image analysis results
    main_df = BKimage_df.merge(
        chqdf,
        how='left',
        left_on='product_ID2',
        right_on='PID'
    )

    # Fill missing identifiers
    main_df[['DEPARTMENT_ID', 'MFG_NO']] = (
        main_df[['DEPARTMENT_ID', 'MFG_NO']].fillna('NA')
    )

    # Remove rows without required hashes
    main_df = main_df.dropna(
        subset=['PHASH', 'COLOR_HASH', 'product_ID2']
    )

    # Detect duplicate images within department and manufacturer groups
    matches = []

    for (dept_id, mfg_no), group in main_df.groupby(
        ['DEPARTMENT_ID', 'MFG_NO']
    ):
        records = group[
            ['product_ID2', 'PHASH', 'COLOR_HASH', 'IMAGE_URL_FINAL']
        ].to_records(index=False)

        for (pid1, ph1, ch1, url1), (pid2, ph2, ch2, url2) in combinations(records, 2):

            # Skip same product comparisons
            if pid1 == pid2:
                continue

            # Structural similarity using perceptual hash
            ph_sim = 1 - (ph1 - ph2) / ph1.hash.size

            # Color similarity using Bhattacharyya distance
            ch_sim = 1 - cv2.compareHist(
                ch1, ch2, cv2.HISTCMP_BHATTACHARYYA
            )

            # Flag potential duplicates based on thresholds
            if ph_sim >= PHASH_THRESH and ch_sim >= COLOR_THRESH:
                matches.append({
                    'product_1': pid1,
                    'product_2': pid2,
                    'url_1': url1,
                    'url_2': url2,
                    'phs_1': ph1,
                    'phs_2': ph2,
                    'cls_1': ch1,
                    'cls_2': ch2,
                    'DEPARTMENT_ID': dept_id,
                    'MFG_NO': mfg_no,
                    'phash_sim': round(ph_sim, 3),
                    'color_sim': round(ch_sim, 3),
                })

    # Convert duplicate matches to DataFrame
    matches_df = pd.DataFrame(matches)

    # Sort duplicates by similarity strength
    if not matches_df.empty:
        matches_df_sorted = matches_df.sort_values(
            by=['phash_sim', 'color_sim'],
            ascending=[False, False]
        )
    else:
        matches_df_sorted = pd.DataFrame()

    # Export results to Excel workbook
    excel_file = (
        f'BImages_Detail_Summary_'
        f'{datetime.today().strftime("%Y_%b_%d_%H%M%S")}.xlsx'
    )

    with pd.ExcelWriter(excel_file, engine='xlsxwriter') as writer:
        broken_images_final.to_excel(writer, sheet_name='BROKEN_IMAGES', index=False)
        swatch_issue_df.to_excel(writer, sheet_name='SWATCH_ISSUE', index=False)
        matches_df_sorted.to_excel(writer, sheet_name='DUPLICATE_ISSUES', index=False)
        mostly_white.to_excel(writer, sheet_name='WHITE_ISSUES', index=False)
        odd_sizes.to_excel(writer, sheet_name='ODD_SIZES', index=False)
        blurry_images.to_excel(writer, sheet_name='BLURRY_IMAGES', index=False)

    # Execution summary metrics
    end_time = datetime.today().strftime("%Y_%b_%d_%H%M%S")
    products_searched = image_df.shape[0]
    products_rendered = BKimage_df.shape[0]
    broken_images = broken_images_final.shape[0]
    swatch_images = swatch_issue_df.shape[0]
    duplicate_images = matches_df_sorted.shape[0]
    white_images = mostly_white.shape[0]
    odd_images = odd_sizes.shape[0]
    blurry_images_ct = blurry_images.shape[0]

    # Execution metadata
    login_name = os.getlogin()
    app_name = 'ImageZen'
    app_ver = 'v1.0'

    # Log execution summary (external logging mechanism)
    log_row = [
        list_save_var, end_time,
        products_searched, products_rendered,
        broken_images, swatch_images,
        duplicate_images, white_images,
        odd_images, blurry_images_ct,
        login_name, app_name, app_ver
    ]

    update_logs(log_row)



def launch_gui():
    """
    Launch the desktop GUI for the image quality analysis application.

    This interface:
    - Collects user credentials
    - Allows selection of data mode (Live / All)
    - Triggers the image analysis pipeline
    - Displays output guidance
    """

    global mode_var

    # Initialize main application window
    root = tk.Tk()
    root.title("ImageZen")
    root.iconbitmap(r"app_icon.ico")
    root.configure(bg="#ffffff")
    root.geometry("620x700")

    # Header bar
    header = tk.Frame(root, bg="#2c3e50", height=40)
    header.pack(fill="x")

    # Application title
    header_title = tk.Label(
        header,
        text="ImageZen - Powered by COE",
        font=("Segoe UI", 20, "bold"),
        fg="white",
        bg="#2c3e50",
        pady=5
    )
    header_title.pack()

    # Tagline displayed below header
    tagline = tk.Label(
        root,
        text="- Elevating Retail Through Insightful Innovation",
        font=("Segoe UI", 10, "italic"),
        fg="#000000",
        bg="#ffffff"
    )
    tagline.pack(pady=(5, 10))

    # Username input field
    tk.Label(
        root,
        text="Snowflake Username:",
        bg="#ffffff",
        fg="#000000",
        font=("Segoe UI", 10)
    ).pack(pady=(10, 0))

    username_entry = tk.Entry(
        root,
        width=40,
        font=("Segoe UI", 10),
        bg="white",
        bd=1,
        highlightthickness=1,
        highlightbackground="black",
        highlightcolor="black"
    )
    username_entry.pack()

    # Password input field (masked)
    tk.Label(
        root,
        text="Snowflake Password:",
        bg="#ffffff",
        fg="#000000",
        font=("Segoe UI", 10)
    ).pack(pady=(20, 0))

    password_entry = tk.Entry(
        root,
        show='*',
        width=40,
        font=("Segoe UI", 10),
        bg="white",
        bd=1,
        highlightthickness=1,
        highlightbackground="black",
        highlightcolor="black"
    )
    password_entry.pack()

    def on_submit():
        """
        Handle submit button click:
        - Validate inputs
        - Perform access and version checks
        - Trigger main processing pipeline
        """
        username = username_entry.get()
        password = password_entry.get()

        # Basic input validation
        if not username or not password:
            messagebox.showerror(
                "Input Error",
                "Please enter both username and password."
            )
            return

        # Access validation
        if username.strip().upper() not in acceptable_users:
            messagebox.showerror(
                "Access Denied",
                "You are not authorized to use this application."
            )
            return

        # Version check
        if current_version != 1:
            messagebox.showerror(
                "Version Error",
                "Upgrade to latest application."
            )
            return

        # Run main processing logic
        try:
            main3(
                pickle_save=f"Partial_output_{datetime.today().strftime('%Y_%m_%d')}",
                userB=username,
                password=password
            )
            output()
        except Exception:
            messagebox.showerror(
                "Error",
                f"An error occurred in main3:\n{traceback.format_exc()}"
            )

    # Button container
    button_frame = tk.Frame(root, bg="#ffffff")
    button_frame.pack(pady=20)

    # Mode selection label
    mode_label = tk.Label(
        button_frame,
        text="Live/All Data:",
        font=("Segoe UI", 10),
        bg="#ffffff",
        fg="#000000"
    )
    mode_label.pack(side="left", padx=(0, 5))

    # Mode selection dropdown
    mode_var = tk.StringVar(value="Live")
    mode_dropdown = ttk.Combobox(
        button_frame,
        textvariable=mode_var,
        values=["Live", "All"],
        font=("Segoe UI", 10),
        state="readonly",
        width=10
    )
    mode_dropdown.pack(side="left", padx=10)

    # Submit button
    submit_btn = tk.Button(
        button_frame,
        text="Submit",
        command=on_submit,
        font=("Segoe UI", 10, "bold"),
        bg="#2c3e50",
        fg="#ffffff",
        activebackground="#1f2a38",
        activeforeground="white",
        bd=0,
        relief="flat",
        padx=20,
        pady=8,
        cursor="hand2"
    )
    submit_btn.pack(side="left", padx=10)

    # Cleanup button to remove intermediate files
    cleanup_btn = tk.Button(
        button_frame,
        text="Cleanup",
        command=cleanup_pickles,
        font=("Segoe UI", 10, "bold"),
        bg="#c0392b",
        fg="#ffffff",
        activebackground="#922b21",
        activeforeground="white",
        bd=0,
        relief="flat",
        padx=20,
        pady=8,
        cursor="hand2"
    )
    cleanup_btn.pack(side="left", padx=10)

    # Output explanation section
    info_label = tk.Label(
        root,
        text=(
            "Output Details:\n"
            "1. Sheet \"BROKEN_IMAGES\" – Shows products with broken image links.\n"
            "2. Sheet \"SWATCH_ISSUE\" – Highlights products with swatch issues.\n"
            "3. Sheet \"DUPLICATE_IMAGES\" – Flags duplicate images across products.\n"
            "4. Sheet \"WHITE_ISSUES\" – Images dominated by white background.\n"
            "5. Sheet \"ODD_SIZES\" – Non-standard image dimensions.\n"
            "6. Sheet \"BLURRY_IMAGES\" – Images failing clarity checks."
        ),
        font=("Segoe UI", 10),
        bg="#1f2a38",
        fg="white",
        justify="left",
        anchor="w"
    )
    info_label.pack(padx=20, pady=(0, 20), anchor="w")

    # Display branding image (optional)
    try:
        img_path = r"C:\Users\MBathija\BI_Engineering\Image_Project\COE.png"
        img = Image.open(img_path)
        img.thumbnail((350, 175))
        img_tk = ImageTk.PhotoImage(img)
        img_label = tk.Label(root, image=img_tk, bg="#ffffff")
        img_label.image = img_tk  # prevent garbage collection
        img_label.pack(pady=(10, 0), anchor="center")
        root.update()
    except Exception as e:
        print(f"Error loading image: {e}")

    # Contact information footer
    contact_label = tk.Label(
        root,
        text=(
            "Please reach out to youremail@domain.com "
            "in case of any issues"
        ),
        font=("Segoe UI", 11),
        fg="#000000",
        bg="#ffffff"
    )
    contact_label.pack(pady=(5, 10))

    # Start GUI event loop
    root.mainloop()


if __name__ == "__main__":

    launch_gui()
