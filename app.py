from flask import Flask, render_template, request, jsonify, send_file
import os
import json
import pandas as pd
from datetime import datetime
import configparser
from groq import Groq
import re
import base64
from PIL import Image
from werkzeug.utils import secure_filename
import io
import PyPDF2
import fitz  # PyMuPDF

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# Create uploads directory if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

class PDFProcessor:
    def __init__(self):
        self.config = configparser.ConfigParser()
        config_file = 'config.ini'
        
        if not os.path.exists(config_file):
            self.create_default_config(config_file)
        
        self.config.read(config_file)
        self.api_key = self.config.get('Groq', 'api_key', fallback='').strip()
        
        self.client = None
        if self.api_key:
            try:
                self.client = Groq(api_key=self.api_key)
                test = self.client.chat.completions.create(
                    model="meta-llama/llama-4-scout-17b-16e-instruct",
                    messages=[{"role": "user", "content": "test"}],
                    max_tokens=1
                )
            except Exception as e:
                print(f"Error initializing Groq client: {e}")
                self.client = None
        else:
            print("No Groq API key found in config.ini")

    def create_default_config(self, config_file):
        self.config['Groq'] = {'api_key': ''}
        with open(config_file, 'w') as f:
            self.config.write(f)

    def save_api_key(self, api_key):
        self.api_key = api_key
        self.config['Groq']['api_key'] = api_key
        with open('config.ini', 'w') as f:
            self.config.write(f)
        try:
            self.client = Groq(api_key=self.api_key)
            return True
        except Exception as e:
            print(f"Failed to initialize Groq client: {e}")
            return False

    def process_pdf(self, pdf_file):
        try:
            # Open PDF with PyMuPDF
            pdf_document = fitz.open(stream=pdf_file.read(), filetype="pdf")
            num_pages = len(pdf_document)
            print(f"\nProcessing PDF with {num_pages} pages")
            
            # Process all pages and store results
            all_pages_data = []
            for page_num in range(num_pages):
                print(f"\nProcessing page {page_num + 1}")
                
                try:
                    # Get the page
                    page = pdf_document[page_num]
                    
                    try:
                        # First try with lower DPI
                        pix = page.get_pixmap(matrix=fitz.Matrix(150/72, 150/72), alpha=False, colorspace=fitz.csRGB)
                    except Exception as e:
                        print(f"First attempt failed: {e}, trying alternative method...")
                        try:
                            # If that fails, try with default matrix
                            pix = page.get_pixmap(alpha=False, colorspace=fitz.csRGB)
                        except Exception as e:
                            print(f"Second attempt failed: {e}, trying final method...")
                            # If that also fails, try without color space specification
                            pix = page.get_pixmap(alpha=False)
                    
                    print(f"Successfully created pixmap with size: {pix.width}x{pix.height}")
                    
                    # Convert pixmap to image data
                    img_data = pix.samples
                    
                    # Create PIL Image from raw bytes
                    img = Image.frombytes('RGB', [pix.width, pix.height], img_data)
                    
                    # Convert to JPEG with high quality
                    img_byte_arr = io.BytesIO()
                    img.save(img_byte_arr, format='JPEG', quality=95, optimize=True)
                    img_byte_arr = img_byte_arr.getvalue()
                    
                    # Convert to base64
                    img_base64 = base64.b64encode(img_byte_arr).decode('utf-8')
                    print(f"Successfully processed page {page_num + 1}")
                    print(f"Base64 length: {len(img_base64)}")
                    
                    # Process image with Llama Scout
                    page_data = self._process_image_with_llama_scout(img_base64, 'jpeg')
                    if page_data:
                        page_data['page_number'] = page_num + 1
                        all_pages_data.append(page_data)
                        
                except Exception as e:
                    print(f"Error processing page {page_num + 1}: {e}")
                    continue
            
            # Close the PDF
            pdf_document.close()
            
            # Create combined data structure
            combined_data = {
                "pages": all_pages_data,
                "metadata": {
                    "total_pages": num_pages,
                    "processed_pages": len(all_pages_data),
                    "processing_date": datetime.now().strftime("%Y-%m-%d"),
                }
            }
            
            return combined_data

        except Exception as e:
            print(f"Error processing PDF: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _process_image_with_llama_scout(self, image_base64, img_format):
        if not self.client:
            raise ValueError("Groq client not initialized. Please check your API key in config.ini")

        try:
            # Create a proper data URL
            data_url = f"data:image/{img_format};base64,{image_base64}"
            
            print("\nSending request to Llama Scout...")
            response = self.client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {
                        "role": "system",
                        "content": """You are an expert at analyzing various types of business and legal documents and extracting structured data.
Your task is to analyze the content and return ONLY a valid JSON object representing the relevant extracted fields based on the document type.
DO NOT include any explanations, markdown, or extra text.
Return ONLY valid JSON"""},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": """analyze the image and use the following example JSON formats for output:

receipt:
{
  "document_type": "receipt",
  "key_fields": {
    "receipt_number": "RCPT-90432",
    "date": "2025-05-20",
    "store_name": "SuperMart",
    "store_address": "123 Elm Street, Springfield",
    "payment_method": "Credit Card",
    "total_paid": 45.99,
    "currency": "USD"
  },
  "tables": [
    {
      "table_name": "Items",
      "items": [
        {
          "description": "Bread",
          "quantity": 1,
          "unit_price": 1.99,
          "line_total": 1.99
        },
        {
          "description": "Snack Pack",
          "quantity": 3,
          "unit_price": 3.0,
          "line_total": 9.0
        }
      ]
    }
  ],
  "notes": "Thank you for shopping with us!"
}

bill:
{
  "document_type": "bill",
  "key_fields": {
    "bill_number": "GRT1715",
    "date": "24/07/21",
    "vendor_name": "Hotel Amer Palace",
    "vendor_address": "Hosangabad Road, Ratanpur, Bhopal - 462046",
    "vendor_gstin": "23AADFH6301L1EN",
    "vendor_state": "Madhya Pradesh"
  },
  "tables": [
    {
      "table_name": "Food Items",
      "items": [
        {
          "description": "BUTTER TANDOOR",
          "quantity": 1,
          "amount": 45.00
        }
      ]
    },
    {
      "table_name": "Additional Charges",
      "items": [
        {
          "description": "Service Charge",
          "amount": 50.00
        }
      ]
    }
  ],
  "totals": {
    "subtotal": 1315.00,
    "cgst": 32.88,
    "sgst": 32.88,
    "total": 1381.00
  }
}"""
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": data_url
                                }
                            }
                        ]
                    }
                ],
                temperature=0.1,
                max_tokens=4000
            )

            result = response.choices[0].message.content.strip()
            print("\n=== RAW MODEL OUTPUT ===")
            print(result)
            print("=== END OF MODEL OUTPUT ===\n")
            
            try:
                data = json.loads(result)
                return data
            except json.JSONDecodeError:
                print("Failed to parse JSON response, attempting to clean up...")
                try:
                    # Try to extract JSON from the response
                    json_matches = re.finditer(r'({(?:[^{}]|{[^{}]*})*})', result)
                    for match in json_matches:
                        try:
                            potential_json = match.group(1)
                            data = json.loads(potential_json)
                            if isinstance(data, dict) and "document_type" in data:
                                return data
                        except:
                            continue
                    
                    # If no valid JSON found, return raw output
                    return {
                        "page_number": 1,  # This will be updated by the caller
                        "raw_output": result
                    }
                except Exception as e:
                    print(f"Failed to extract JSON: {e}")
                    return {
                        "page_number": 1,
                        "raw_output": result
                    }

        except Exception as e:
            print(f"Groq API error: {e}")
            print(f"Error details: {str(e)}")
            return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/process', methods=['POST'])
def process_pdf():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not file.filename.endswith('.pdf'):
        return jsonify({'error': 'Only PDF files are allowed'}), 400

    try:
        processor = PDFProcessor()
        data = processor.process_pdf(file)

        if data:
            return jsonify(data)
        else:
            return jsonify({'error': 'Failed to process PDF'}), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/export', methods=['POST'])
def export_data():
    try:
        data = request.json
        output = f"extracted_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        
        # Create a Pandas Excel writer
        with pd.ExcelWriter(output) as writer:
            # Write summary sheet
            summary_data = {
                'Total Pages': data['metadata']['total_pages'],
                'Processed Pages': data['metadata']['processed_pages'],
                'Processing Date': data['metadata']['processing_date']
            }
            pd.DataFrame([summary_data]).to_excel(writer, sheet_name='Summary', index=False)
            
            # Write individual page data
            for page_data in data['pages']:
                page_num = page_data['page_number']
                
                # Write key fields
                key_fields_df = pd.DataFrame([page_data.get('key_fields', {})])
                key_fields_df.to_excel(writer, sheet_name=f'Page {page_num} - Fields', index=False)
                
                # Write tables if present
                if 'tables' in page_data and page_data['tables']:
                    for table_idx, table in enumerate(page_data['tables']):
                        if 'items' in table and table['items']:
                            sheet_name = f'Page {page_num} - {table["table_name"]}'
                            # Truncate sheet name if too long (Excel limitation)
                            if len(sheet_name) > 31:
                                sheet_name = f'Page {page_num} - Table {table_idx + 1}'
                            items_df = pd.DataFrame(table['items'])
                            items_df.to_excel(writer, sheet_name=sheet_name, index=False)
                
                # Handle legacy format where items are directly in the page_data
                elif 'items' in page_data and page_data['items']:
                    items_df = pd.DataFrame(page_data['items'])
                    items_df.to_excel(writer, sheet_name=f'Page {page_num} - Items', index=False)
        
        return send_file(output, as_attachment=True, download_name=output)
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5000, use_reloader=False) 