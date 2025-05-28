import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from tkinterdnd2 import DND_FILES, TkinterDnD
import spacy
import tabula
import pandas as pd
import os
import re
import numpy as np
from price_parser import Price  # For reliable price extraction
from typing import List, Dict, Tuple, Any
import cv2
from PIL import Image
import pytesseract
import json
from groq import Groq
import ast
import configparser
import base64
from datetime import datetime

# Updated PDF text extraction libraries
from pypdf import PdfReader # Successor to PyPDF4
import fitz # PyMuPDF
import pdfplumber # Great for text and some table detection

# OCR Engine
import easyocr
import numpy as np # For EasyOCR image conversion
from PIL import Image # For image manipulation with EasyOCR

class PDFExtractorGUI:
    def __init__(self, master):
        self.master = master
        master.title("WVF Intelligent Data Processor v0.3_Alpha Edition")
        master.geometry("1100x700") # Adjust initial window size

        self.file_path = ""
        self.extracted_text = ""
        self.processed_entities = []

        # Load Groq API key from config
        self.config = configparser.ConfigParser()
        config_file = 'config.ini'
        
        if not os.path.exists(config_file):
            self.create_default_config(config_file)
        
        self.config.read(config_file)
        self.api_key = self.config.get('Groq', 'api_key', fallback='')
        
        if not self.api_key:
            self.prompt_for_api_key()

        # Initialize Groq client
        try:
            self.client = Groq(api_key=self.api_key)
        except Exception as e:
            print(f"Error initializing Groq client: {e}")
            self.client = None

        # Initialize EasyOCR reader once
        try:
            self.reader = easyocr.Reader(['en']) # 'en' for English. Add more languages if needed, e.g., ['en', 'fr']
            self.master.after(100, lambda: messagebox.showinfo("EasyOCR", "EasyOCR model loaded successfully. First run might download models."))
        except Exception as e:
            messagebox.showerror("EasyOCR Error", f"Failed to load EasyOCR. Please check installation and network: {e}")
            self.reader = None

        # --- GUI Layout ---
        # File Selection Frame
        file_frame = ttk.LabelFrame(master, text="File Selection")
        file_frame.grid(row=0, column=0, columnspan=2, padx=10, pady=5, sticky="ew")

        self.file_label = ttk.Label(file_frame, text="No file selected.", wraplength=400)
        self.file_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")

        self.browse_button = ttk.Button(file_frame, text="Browse", command=self.browse_file)
        self.browse_button.grid(row=0, column=1, padx=5, pady=5)

        # Dropzone (positioned centrally/right)
        self.dropzone = tk.Label(master, text="Drop files here", width=30, height=10, relief=tk.GROOVE, bg="#ADD8E6")
        self.dropzone.grid(row=0, column=2, rowspan=3, padx=10, pady=10, sticky="nsew") # make it expandable

        self.dropzone.drop_target_register(DND_FILES)
        self.dropzone.dnd_bind('<<Drop>>', self.handle_drop)

        # Extraction and Transformer Tools Frame
        tools_frame = ttk.LabelFrame(master, text="Processing Options")
        tools_frame.grid(row=1, column=0, columnspan=2, padx=10, pady=5, sticky="ew")

        self.extraction_tool_label = ttk.Label(tools_frame, text="Text Extraction Tool:")
        self.extraction_tool_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.extraction_tool_var = tk.StringVar(value="PyMuPDF (Fast)") # Default to PyMuPDF
        self.extraction_tool_options = ["PyMuPDF (Fast)", "pdfplumber (Detailed)", "PDFMiner.six", "pypdf (Basic)"]
        self.extraction_tool_dropdown = ttk.OptionMenu(tools_frame, self.extraction_tool_var, self.extraction_tool_options[0], *self.extraction_tool_options)
        self.extraction_tool_dropdown.grid(row=0, column=1, padx=5, pady=5, sticky="ew")

        self.transformer_label = ttk.Label(tools_frame, text="NLP Transformer:")
        self.transformer_label.grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.transformer_var = tk.StringVar(value="SpaCy (NER)") # Default to SpaCy
        self.transformer_options = ["SpaCy (NER)"] # Can add more later if needed
        self.transformer_dropdown = ttk.OptionMenu(tools_frame, self.transformer_var, self.transformer_options[0], *self.transformer_options)
        self.transformer_dropdown.grid(row=1, column=1, padx=5, pady=5, sticky="ew")

        # OCR Checkbox
        self.use_ocr_var = tk.BooleanVar(value=False)
        self.use_ocr_checkbox = ttk.Checkbutton(tools_frame, text="Attempt OCR if text extraction is poor", variable=self.use_ocr_var)
        self.use_ocr_checkbox.grid(row=2, column=0, columnspan=2, padx=5, pady=5, sticky="w")

        # Buttons Frame
        button_frame = ttk.Frame(master)
        button_frame.grid(row=3, column=0, columnspan=3, padx=10, pady=5, sticky="ew")
        button_frame.columnconfigure(0, weight=1)
        button_frame.columnconfigure(1, weight=1)

        self.extract_button = ttk.Button(button_frame, text="Extract & Process Text", command=self.extract_and_process_text)
        self.extract_button.grid(row=0, column=0, padx=5, pady=5, sticky="ew")

        self.export_button = ttk.Button(button_frame, text="Extract Tables & Export Excel", command=self.export_excel)
        self.export_button.grid(row=0, column=1, padx=5, pady=5, sticky="ew")


        # Text Display Frames
        text_display_frame = ttk.LabelFrame(master, text="Extracted Text")
        text_display_frame.grid(row=4, column=0, columnspan=2, padx=10, pady=5, sticky="nsew")
        text_display_frame.grid_rowconfigure(0, weight=1)
        text_display_frame.grid_columnconfigure(0, weight=1)

        self.text_box = tk.Text(text_display_frame, wrap=tk.WORD, height=15, width=60) # Increased width
        self.text_box.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
        text_scroll = ttk.Scrollbar(text_display_frame, command=self.text_box.yview)
        text_scroll.grid(row=0, column=1, sticky="ns")
        self.text_box.config(yscrollcommand=text_scroll.set)

        processed_text_display_frame = ttk.LabelFrame(master, text="Processed Data (SpaCy Entities)")
        processed_text_display_frame.grid(row=4, column=2, padx=10, pady=5, sticky="nsew")
        processed_text_display_frame.grid_rowconfigure(0, weight=1)
        processed_text_display_frame.grid_columnconfigure(0, weight=1)

        self.processed_text_box = tk.Text(processed_text_display_frame, wrap=tk.WORD, height=15, width=60) # Increased width
        self.processed_text_box.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
        processed_text_scroll = ttk.Scrollbar(processed_text_display_frame, command=self.processed_text_box.yview)
        processed_text_scroll.grid(row=0, column=1, sticky="ns")
        self.processed_text_box.config(yscrollcommand=processed_text_scroll.set)


        # Configure row and column weights for responsiveness
        master.grid_rowconfigure(4, weight=1) # Make text boxes expand vertically
        master.grid_columnconfigure(0, weight=1)
        master.grid_columnconfigure(1, weight=1)
        master.grid_columnconfigure(2, weight=1)


    def browse_file(self):
        file_path = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
        if file_path:
            self.file_path = file_path
            self.file_label.config(text=os.path.basename(self.file_path)) # Display only filename
            self.clear_text_boxes()

    def handle_drop(self, event):
        # tkinterdnd2 provides path as a list of strings if multiple, or a single string with {} around it
        # for single file drops on Windows. We need to clean this up.
        files = event.data
        if files.startswith('{') and files.endswith('}'):
            self.file_path = files[1:-1] # Remove curly braces
        else:
            self.file_path = files.strip() # Remove any leading/trailing whitespace

        if os.path.exists(self.file_path) and self.file_path.lower().endswith(".pdf"):
            self.file_label.config(text=os.path.basename(self.file_path))
            self.clear_text_boxes()
        else:
            messagebox.showerror("Invalid File", "Please drop a valid PDF file.")
            self.file_path = ""
            self.file_label.config(text="No file selected.")

    def clear_text_boxes(self):
        self.text_box.delete('1.0', tk.END)
        self.processed_text_box.delete('1.0', tk.END)
        self.extracted_text = ""
        self.processed_entities = []

    def extract_and_process_text(self):
        if not self.file_path:
            messagebox.showwarning("No File", "Please select or drop a PDF file first.")
            return

        try:
            # Process PDF directly with Groq
            data = self._process_pdf_with_groq(self.file_path)
            if not data:
                return

            # Display extracted text in the GUI
            self.text_box.delete('1.0', tk.END)
            self.processed_text_box.delete('1.0', tk.END)

            # Display document info
            self.text_box.insert(tk.END, "Document Information:\n\n")
            for key, value in data.items():
                if key not in ['items', 'metadata']:
                    self.text_box.insert(tk.END, f"{key}: {value}\n")

            # Display processed items
            self.processed_text_box.insert(tk.END, "Line Items:\n\n")
            if 'items' in data and data['items']:
                for item in data['items']:
                    self.processed_text_box.insert(tk.END, f"Description: {item.get('description', 'N/A')}\n")
                    self.processed_text_box.insert(tk.END, f"Quantity: {item.get('quantity', 'N/A')}\n")
                    self.processed_text_box.insert(tk.END, f"Unit Price: {item.get('unit_price', 'N/A')}\n")
                    self.processed_text_box.insert(tk.END, f"Line Total: {item.get('line_total', 'N/A')}\n")
                    self.processed_text_box.insert(tk.END, "-" * 40 + "\n")

        except Exception as e:
            messagebox.showerror("Processing Error", f"An error occurred: {e}")
            self.text_box.insert(tk.END, f"Error: {e}\n")

    def _convert_pdf_to_images(self, pdf_path: str) -> List[str]:
        """
        Convert PDF pages to images and save them temporarily.
        Returns a list of temporary image file paths.
        """
        temp_images = []
        try:
            with fitz.open(pdf_path) as doc:
            for page_num in range(len(doc)):
                    page = doc[page_num]
                    pix = page.get_pixmap(matrix=fitz.Matrix(300/72, 300/72))  # 300 DPI
                    temp_img_path = f"temp_page_{page_num}.png"
                    pix.save(temp_img_path)
                    temp_images.append(temp_img_path)
            return temp_images
        except Exception as e:
            print(f"Error converting PDF to images: {e}")
            return []

    def _encode_image_base64(self, image_path: str) -> str:
        """
        Encode image to base64 string.
        """
        try:
            with open(image_path, "rb") as image_file:
                return base64.b64encode(image_file.read()).decode('utf-8')
        except Exception as e:
            print(f"Error encoding image: {e}")
            return ""

    def _process_pdf_with_groq(self, pdf_path: str) -> dict:
        """
        Process PDF using Groq, handling both traditional PDFs and image-based PDFs.
        Returns structured data as a dictionary.
        """
        try:
            # First try traditional PDF text extraction
            pdf_content = ""
            is_image_based = False
            
            try:
                with fitz.open(pdf_path) as doc:
                    # Try text extraction
                    for page_num in range(len(doc)):
                        page = doc[page_num]
                        text = page.get_text()
                        if text.strip():
                            pdf_content += text + "\n\n"
                    
                    # If no text found, treat as image-based PDF
                    if not pdf_content.strip():
                        is_image_based = True
            except Exception as e:
                print(f"PDF text extraction failed: {e}")
                is_image_based = True

            if is_image_based:
                # Convert PDF to images
                image_paths = self._convert_pdf_to_images(pdf_path)
                if not image_paths:
                    raise ValueError("Failed to convert PDF to images")

                try:
                    # Process each page as an image
                    combined_data = {
                        "document_type": None,
                        "items": [],
                        "metadata": {
                            "page_count": len(image_paths),
                            "processing_date": datetime.now().strftime("%Y-%m-%d"),
                            "confidence_score": 0.0
                        }
                    }

                    for idx, img_path in enumerate(image_paths):
                        # Encode image to base64
                        image_base64 = self._encode_image_base64(img_path)
                        if not image_base64:
                            continue

                        # Create a temporary URL for the image
                        image_url = f"data:image/png;base64,{image_base64}"

                        # Process with Groq's vision model
                        response = self.client.chat.completions.create(
                            model="llama3-70b-8192",  # Update with actual vision model when available
                            messages=[
                                {
                                    "role": "system",
                                    "content": """You are an expert at analyzing document images and extracting structured data.
                                    Your task is to analyze the document image, understand its layout and structure, and extract information accurately."""
                                },
                                {
                                    "role": "user",
                                    "content": f"""Analyze this OCR text and extract structured data:

{self.reader.readtext(img_path, detail=0)}

Extract the following information in JSON format:

{{
    "document_type": "invoice/bill/receipt",
    "document_number": "TEXT_OR_NUMBER",
    "date": "YYYY-MM-DD",
    "vendor_name": "TEXT",
    "vendor_address": "TEXT",
    "vendor_tax_id": "TEXT",
    "customer_name": "TEXT",
    "customer_address": "TEXT",
    "customer_tax_id": "TEXT",
    "currency": "TEXT_CODE_USD_EUR_INR",
    "subtotal": 0.0,
    "tax_amount": 0.0,
    "total_amount": 0.0,
    "due_date": "YYYY-MM-DD",
    "payment_terms": "TEXT",
    "items": [
        {{
            "description": "TEXT",
            "quantity": 0.0,
            "unit_price": 0.0,
            "line_total": 0.0
        }}
    ],
    "notes": "TEXT"
}}"""
                                }
                            ],
                            temperature=0.1,
                            max_tokens=4000
                        )

                        # Parse response
                        try:
                            result = response.choices[0].message.content
                            # Try to extract JSON from the response
                            try:
                                # First try direct JSON parsing
                                page_data = json.loads(result)
                            except json.JSONDecodeError:
                                # If that fails, try to extract JSON from the text
                                json_match = re.search(r'({[\s\S]*})', result)
                                if json_match:
                                    try:
                                        page_data = json.loads(json_match.group(1))
                                    except:
                                        raise ValueError("Could not parse JSON from response")
                                else:
                                    raise ValueError("No JSON found in response")
                        except Exception as parse_error:
                            print(f"Error parsing page {idx + 1}: {parse_error}")
                            continue
                        
                        # Merge data from multiple pages
                        if idx == 0:
                            # Use first page for document-level information
                            combined_data.update({k: v for k, v in page_data.items() 
                                               if k not in ['items', 'metadata']})
                        
                        # Append items from each page
                        if 'items' in page_data:
                            combined_data['items'].extend(page_data['items'])

                        # Clean up temporary image
                        try:
                            os.remove(img_path)
                        except:
                            pass

                    # Save JSON output
                    output_dir = "./extracted_data"
                    os.makedirs(output_dir, exist_ok=True)
                    base_filename = os.path.splitext(os.path.basename(pdf_path))[0]
                    json_path = os.path.join(output_dir, f"{base_filename}_data.json")
                    
                    with open(json_path, 'w', encoding='utf-8') as f:
                        json.dump(combined_data, f, indent=2, ensure_ascii=False)

                    return combined_data

                except Exception as e:
                    raise ValueError(f"Vision processing failed: {str(e)}")
                finally:
                    # Ensure cleanup of any remaining temporary files
                    for img_path in image_paths:
                        try:
                            if os.path.exists(img_path):
                                os.remove(img_path)
                        except:
                            pass

            else:
                # Process traditional PDF with text content
                try:
                    response = self.client.chat.completions.create(
                        model="llama3-70b-8192",
                        messages=[
                            {
                                "role": "system",
                                "content": """You are an expert at analyzing PDF documents and extracting structured data.
                                Your task is to analyze the document content and extract ONLY the requested JSON data structure.
                                DO NOT include any additional text, explanations, or markdown formatting in your response.
                                ONLY return valid JSON."""
                            },
                            {
                                "role": "user",
                                "content": f"""Extract the following information from this document in strict JSON format:

{{
    "document_type": "invoice/bill/receipt",
    "document_number": "TEXT_OR_NUMBER",
    "date": "YYYY-MM-DD",
    "vendor_name": "TEXT",
    "vendor_address": "TEXT",
    "vendor_tax_id": "TEXT",
    "customer_name": "TEXT",
    "customer_address": "TEXT",
    "customer_tax_id": "TEXT",
    "currency": "TEXT_CODE_USD_EUR_INR",
    "subtotal": 0.0,
    "tax_amount": 0.0,
    "total_amount": 0.0,
    "due_date": "YYYY-MM-DD",
    "payment_terms": "TEXT",
    "items": [
        {{
            "description": "TEXT",
            "quantity": 0.0,
            "unit_price": 0.0,
            "line_total": 0.0
        }}
    ],
    "notes": "TEXT"
}}

Document content:
{pdf_content}"""
                            }
                        ],
                        temperature=0.1,
                        max_tokens=4000
                    )

                    # Parse response
                    result = response.choices[0].message.content.strip()
                    
                    # Try multiple approaches to extract and parse JSON
                    data = None
                    
                    # Attempt 1: Direct JSON parsing
                    try:
                        data = json.loads(result)
                    except json.JSONDecodeError:
                        # Attempt 2: Find JSON-like structure with regex
                        json_matches = re.finditer(r'({(?:[^{}]|{[^{}]*})*})', result)
                        for match in json_matches:
                            try:
                                potential_json = match.group(1)
                                data = json.loads(potential_json)
                                if isinstance(data, dict) and "document_type" in data:
                                    break
                            except:
                                continue
                        
                        # Attempt 3: Try to fix common JSON formatting issues
                        if not data:
                            try:
                                # Replace common formatting issues
                                cleaned_result = result.replace("'", '"')  # Replace single quotes
                                cleaned_result = re.sub(r'(\w+):', r'"\1":', cleaned_result)  # Quote unquoted keys
                                cleaned_result = re.sub(r',\s*}', '}', cleaned_result)  # Remove trailing commas
                                data = json.loads(cleaned_result)
                            except:
                                pass
                        
                        if not data:
                            raise ValueError("Could not parse valid JSON from response")

                    # Add metadata
                    if 'metadata' not in data:
                        data['metadata'] = {
                            "page_count": len(pdf_content.split('\f')),  # Form feed character
                            "processing_date": datetime.now().strftime("%Y-%m-%d"),
                            "confidence_score": 0.8  # Default confidence for text-based extraction
                        }

                    # Save JSON output
                    output_dir = "./extracted_data"
                    os.makedirs(output_dir, exist_ok=True)
                    base_filename = os.path.splitext(os.path.basename(pdf_path))[0]
                    json_path = os.path.join(output_dir, f"{base_filename}_data.json")
                    
                    with open(json_path, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)

                    return data

                except Exception as e:
                    print(f"Text processing error details: {str(e)}")
                    print(f"Raw response: {result if 'result' in locals() else 'No response'}")
                    raise ValueError(f"Text processing failed: {str(e)}")

        except Exception as e:
            print(f"PDF processing error: {e}")
            messagebox.showerror("Processing Error", str(e))
            return None

    def export_excel(self):
        if not self.file_path:
            messagebox.showwarning("No File", "Please select or drop a PDF file first.")
            return

        try:
            # Process PDF directly with Groq
            data = self._process_pdf_with_groq(self.file_path)
            if not data:
                return

            # Prepare Excel export
            output_dir = "./extracted_data"
            os.makedirs(output_dir, exist_ok=True)
            base_filename = os.path.splitext(os.path.basename(self.file_path))[0]
            output_path = os.path.join(output_dir, f"{base_filename}_processed.xlsx")

            with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
                workbook = writer.book
                
                # Define formats
                header_format = workbook.add_format({
                    'bold': True,
                    'bg_color': '#D3D3D3',
                    'border': 1
                })
                date_format = workbook.add_format({'num_format': 'yyyy-mm-dd'})
                currency_format = workbook.add_format({'num_format': '#,##0.00'})
                
                # Create Document Information sheet
                doc_info = {k: v for k, v in data.items() if k != 'items' and k != 'metadata'}
                doc_df = pd.DataFrame([doc_info])
                doc_df.to_excel(writer, sheet_name='Document Info', index=False)
                
                # Format Document Info sheet
                doc_sheet = writer.sheets['Document Info']
                for idx, col in enumerate(doc_df.columns):
                    doc_sheet.write(0, idx, col, header_format)
                    if col in ['date', 'due_date']:
                        doc_sheet.set_column(idx, idx, None, date_format)
                    elif col in ['subtotal', 'tax_amount', 'total_amount']:
                        doc_sheet.set_column(idx, idx, None, currency_format)
                
                # Create Line Items sheet
                if 'items' in data and data['items']:
                    items_df = pd.DataFrame(data['items'])
                    items_df.to_excel(writer, sheet_name='Line Items', index=False)
                    
                    # Format Line Items sheet
                    items_sheet = writer.sheets['Line Items']
                    for idx, col in enumerate(items_df.columns):
                        items_sheet.write(0, idx, col, header_format)
                        if col in ['unit_price', 'line_total']:
                            items_sheet.set_column(idx, idx, None, currency_format)
                    
                    # Add totals row
                    if 'line_total' in items_df.columns:
                        total_row = len(items_df) + 2
                        items_sheet.write(total_row, 0, 'Total', header_format)
                        total_formula = f'=SUM(D2:D{len(items_df)+1})'
                        items_sheet.write_formula(total_row, 3, total_formula, currency_format)
                
                # Create Metadata sheet
                if 'metadata' in data:
                    meta_df = pd.DataFrame([data['metadata']])
                    meta_df.to_excel(writer, sheet_name='Metadata', index=False)
                    
                    meta_sheet = writer.sheets['Metadata']
                    for idx, col in enumerate(meta_df.columns):
                        meta_sheet.write(0, idx, col, header_format)
                        if col == 'processing_date':
                            meta_sheet.set_column(idx, idx, None, date_format)

            messagebox.showinfo("Export Successful", 
                              f"Data extracted and saved to:\n{output_path}\n\nJSON data saved to:\n{os.path.join(output_dir, f'{base_filename}_data.json')}")

        except Exception as e:
            messagebox.showerror("Processing Error", f"An error occurred: {e}")

    def create_default_config(self, config_file):
        self.config['Groq'] = {'api_key': ''}
        with open(config_file, 'w') as f:
            self.config.write(f)

    def prompt_for_api_key(self):
        key = simpledialog.askstring("Groq API Key", 
                                   "Please enter your Groq API key:",
                                   parent=self.master)
        if key:
            self.api_key = key
            self.config['Groq']['api_key'] = key
            with open('config.ini', 'w') as f:
                self.config.write(f)
            # Initialize Groq client with new key
            try:
                self.client = Groq(api_key=self.api_key)
            except Exception as e:
                messagebox.showerror("API Error", f"Failed to initialize Groq client: {e}")

# --- Main Application Setup ---
if __name__ == "__main__":
    root = TkinterDnD.Tk()
    root.overrideredirect(False)
    try:
        # Ensure the icon path is correct and accessible.
        # It's better to place the icon file in the same directory as the script
        # or provide a relative path.
        icon_path = os.path.join(os.path.dirname(__file__), "yrc-removebg-preview.png")
        if os.path.exists(icon_path):
            root.iconphoto(True, tk.PhotoImage(file=icon_path))
        else:
            print(f"Warning: Icon file not found at {icon_path}, using default icon")
    except Exception as e:
        print(f"Warning: Could not set icon: {e}, using default icon")

    pdf_extractor = PDFExtractorGUI(root)
    root.mainloop()