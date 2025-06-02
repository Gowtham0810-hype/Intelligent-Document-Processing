from flask import Flask, render_template, request, jsonify, send_file
import os
import json
import pandas as pd
from datetime import datetime
import fitz
import easyocr
import configparser
from groq import Groq
import re
import base64
from PIL import Image
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

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
                    model="llama3-70b-8192",
                    messages=[{"role": "user", "content": "test"}],
                    max_tokens=1
                )
            except Exception as e:
                print(f"Error initializing Groq client: {e}")
                self.client = None
        else:
            print("No Groq API key found in config.ini")
        
        try:
            self.reader = easyocr.Reader(['en'])
        except Exception as e:
            print(f"Failed to load EasyOCR: {e}")
            self.reader = None

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
            temp_pdf_path = f"temp_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            with open(temp_pdf_path, "wb") as f:
                f.write(pdf_file.getvalue())

            data = self._process_pdf_with_groq(temp_pdf_path)
            
            os.remove(temp_pdf_path)
            
            return data
        except Exception as e:
            print(f"Error processing PDF: {e}")
            return None

    def _process_pdf_with_groq(self, pdf_path):
        try:
            pages_data = []
            combined_data = {
                "pages": [],
                "metadata": {
                    "total_pages": 0,
                    "processing_date": datetime.now().strftime("%Y-%m-%d"),
                    "filename": os.path.basename(pdf_path)
                }
            }
            
            with fitz.open(pdf_path) as doc:
                combined_data["metadata"]["total_pages"] = len(doc)
                
                for page_num in range(len(doc)):
                    page = doc[page_num]
                    text = page.get_text()
                    page_data = None
                    
                    if text.strip():
                        # Text-based page
                        print(f"\n=== Processing Page {page_num + 1} (Text-based) ===")
                        page_data = self._process_text_with_groq(text)
                    else:
                        # Image-based page
                        print(f"\n=== Processing Page {page_num + 1} (Image-based) ===")
                        pix = page.get_pixmap(matrix=fitz.Matrix(300/72, 300/72))
                        temp_img_path = f"temp_page_{page_num}.png"
                        pix.save(temp_img_path)
                        
                        if self.reader:
                            ocr_text = "\n".join(self.reader.readtext(temp_img_path, detail=0))
                            page_data = self._process_text_with_groq(ocr_text)
                        
                        try:
                            os.remove(temp_img_path)
                        except:
                            pass
                    
                    if page_data:
                        page_data["page_number"] = page_num + 1
                        combined_data["pages"].append(page_data)

            return combined_data

        except Exception as e:
            print(f"PDF processing error: {e}")
            return None

    def _process_text_with_groq(self, text_content):
        if not self.client:
            raise ValueError("Groq client not initialized. Please check your API key in config.ini")

        try:
            response = self.client.chat.completions.create(
                model="llama3-70b-8192",
                messages=[
                    {
                        "role": "system",
                        "content": """You are an expert at analyzing various types of business and legal documents and extracting structured data.
Your task is to analyze the content and return ONLY a valid JSON object representing the relevant extracted fields based on the document type.
DO NOT include any explanations, markdown, or extra text.
Return ONLY valid JSON"""
                    },
                    {
                        "role": "user",
                        "content": f"""example JSON formats:
receipt:
{{{{
  "document_type": "receipt",
  "key_fields": {{{{
    "receipt_number": "RCPT-90432",
    "date": "2025-05-20",
    "store_name": "SuperMart",
    "store_address": "123 Elm Street, Springfield",
    "payment_method": "Credit Card",
    "total_paid": 45.99,
    "currency": "USD"
  }}}},
  "items": [
    {{{{
      "description": "Bread",
      "quantity": 1,
      "unit_price": 1.99,
      "line_total": 1.99
    }}}},
    {{{{
      "description": "Snack Pack",
      "quantity": 3,
      "unit_price": 3.0,
      "line_total": 9.0
    }}}}
  ],
  "notes": "Thank you for shopping with us!"
}}}}

bill:
{{{{
  "document_type": "bill",
  "key_fields": {{{{
    "bill_number": "GRT1715",
    "date": "24/07/21",
    "vendor_name": "Hotel Amer Palace",
    "vendor_address": "Hosangabad Road, Ratanpur, Bhopal - 462046",
    "vendor_gstin": "23AADFH6301L1EN",
    "vendor_state": "Madhya Pradesh"
  }}}},
  "items": [
    {{{{
      "description": "BUTTER TANDOOR",
      "quantity": 1,
      "amount": 45.00
    }}}}
  ],
  "totals": {{{{
    "subtotal": 1315.00,
    "cgst": 32.88,
    "sgst": 32.88,
    "total": 1381.00
  }}}}
}}}}

Analyze the following content and return a similar JSON structure:
{text_content}"""
                    }
                ],
                temperature=0.1,
                max_tokens=4000
            )

            result = response.choices[0].message.content.strip()
            
            data = None
            
            try:
                data = json.loads(result)
            except json.JSONDecodeError:
                json_matches = re.finditer(r'({(?:[^{}]|{[^{}]*})*})', result)
                for match in json_matches:
                    try:
                        potential_json = match.group(1)
                        data = json.loads(potential_json)
                        if isinstance(data, dict) and "document_type" in data:
                            break
                    except:
                        continue
                
                if not data:
                    try:
                        cleaned_result = result.replace("'", '"')
                        cleaned_result = re.sub(r'(\w+):', r'"\1":', cleaned_result)
                        cleaned_result = re.sub(r',\s*}', '}', cleaned_result)
                        data = json.loads(cleaned_result)
                    except:
                        pass
                
                if not data:
                    raise ValueError("Could not parse valid JSON from response")

            return data

        except Exception as e:
            print(f"Groq API error: {e}")
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
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        processor = PDFProcessor()
        data = processor._process_pdf_with_groq(filepath)

        os.remove(filepath)

        if data:
            return jsonify(data)
        else:
            return jsonify({'error': 'Failed to process PDF'}), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/export/<format>', methods=['POST'])
def export_data(format):
    try:
        data = request.json
        is_page = request.args.get('is_page', 'false').lower() == 'true'
        page_number = request.args.get('page_number', None)
        
        if format == 'excel':
            if is_page and page_number:
                output = f"page_{page_number}_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            else:
                output = f"complete_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            
            with pd.ExcelWriter(output) as writer:
                if is_page and page_number:
                    # Export single page data
                    doc_info = {k: v for k, v in data.items() if k not in ['items', 'metadata']}
                    pd.DataFrame([doc_info]).to_excel(writer, sheet_name='Document Info', index=False)
                    
                    if 'items' in data and data['items']:
                        pd.DataFrame(data['items']).to_excel(writer, sheet_name='Line Items', index=False)
                else:
                    # Export all pages data
                    metadata = pd.DataFrame([data['metadata']])
                    metadata.to_excel(writer, sheet_name='Metadata', index=False)
                    
                    for page in data['pages']:
                        page_num = page['page_number']
                        sheet_prefix = f'Page {page_num}'
                        
                        # Document info for the page
                        doc_info = {k: v for k, v in page.items() if k not in ['items', 'page_number']}
                        pd.DataFrame([doc_info]).to_excel(writer, sheet_name=f'{sheet_prefix} Info', index=False)
                        
                        # Line items for the page
                        if 'items' in page and page['items']:
                            pd.DataFrame(page['items']).to_excel(writer, sheet_name=f'{sheet_prefix} Items', index=False)
            
            return send_file(output, as_attachment=True, download_name=output)
            
        elif format == 'json':
            if is_page and page_number:
                output = f"page_{page_number}_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            else:
                output = f"complete_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            
            with open(output, 'w') as f:
                json.dump(data, f, indent=2)
            
            return send_file(output, as_attachment=True, download_name=output)
        
        else:
            return jsonify({'error': 'Invalid export format'}), 400

    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5000, use_reloader=False) 