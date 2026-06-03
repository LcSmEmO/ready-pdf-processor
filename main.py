from fastapi import FastAPI, UploadFile, File
from unstructured.partition.pdf import partition_pdf
import tempfile
import os
import re

app = FastAPI()

#Function to fix duplicated letters in the text
def fix_duplicated_letters(text: str) -> str:
    words = text.split()
    fixed_words = []

    for word in words:
        # Se tiver número, não mexe.
        # Isso evita estragar datas.
        if any(char.isdigit() for char in word):
            fixed_words.append(word)
            continue

        # Corrige casos curtos em título.
        if len(word) == 2 and word[0] == word[1] and word.isupper():
            fixed_words.append(word[0])
            continue

        # Só tenta corrigir palavras longas.
        if len(word) < 6:
            fixed_words.append(word)
            continue

        collapsed = ""
        duplicated_pairs = 0
        total_pairs = 0
        i = 0

        while i < len(word):
            if i + 1 < len(word):
                total_pairs += 1

                if word[i] == word[i + 1]:
                    collapsed += word[i]
                    duplicated_pairs += 1
                    i += 2
                    continue

            collapsed += word[i]
            i += 1

        # Só aceita a correção se a maioria da palavra estiver duplicada.
        if total_pairs > 0 and duplicated_pairs / total_pairs >= 0.7:
            fixed_words.append(collapsed)
        else:
            fixed_words.append(word)

    return " ".join(fixed_words)

#Function to fix hyphenated words in the text
def fix_hyphenated_words(text: str) -> str:
    # Corrige casos como: afir- mo -> afirmo
    text = re.sub(r"(\w)-\s+(\w)", r"\1\2", text)

    return text

#Function to clean the extracted text by fixing duplicated letters, hyphenated words, and normalizing whitespace
def clean_extracted_text(text: str) -> str:
    ligature_replacements = {
        "ﬁ": "fi",
        "ﬂ": "fl",
        "ﬀ": "ff",
        "ﬃ": "ffi",
        "ﬄ": "ffl",
    }

    for ligature, replacement in ligature_replacements.items():
        text = text.replace(ligature, replacement)

    text = fix_duplicated_letters(text)
    text = fix_hyphenated_words(text)
    text = re.sub(r"\s+", " ", text).strip()

    return text

#Function to determine if a text block is likely a page number based on its content and the page number metadata
def is_page_number_block(text: str, page: int | None) -> bool:
    clean = text.strip()

    if not clean.isdigit():
        return False

    if page is None:
        return len(clean) <= 3

    return int(clean) == page or len(clean) <= 3

#Function to remove trailing page numbers from text blocks, while preserving titles and common references
def remove_trailing_page_number(text: str, category: str) -> str:
    clean = text.strip()

    # Não mexe em títulos
    if category == "Title":
        return clean

    # Se o bloco inteiro for só um número pequeno, remove
    if re.fullmatch(r"\d{1,3}", clean):
        return ""

    # Evita remover referências bibliográficas comuns.
    if re.search(r"(p\.|pág\.|pag\.|cap\.|capítulo)\s+\d{1,3}$", clean, re.IGNORECASE):
        return clean

    # Remove número pequeno no final do bloco.
    clean = re.sub(r"\s+\d{1,3}$", "", clean)

    return clean

#Function to map the block type based on the category
def map_block_type(category: str) -> str:
    types = {
        "Title": "title",
        "NarrativeText": "paragraph",
        "ListItem": "list_item",
        "Table": "table",
        "Header": "header",
        "Footer": "footer",
        "PageNumber": "page_number"
    }

    return types.get(category, "text")

#Function to determine if a text block is likely a footnote based on its content and common patterns
def is_footnote_block(text: str) -> bool:
    clean = text.strip()

    if re.match(r"^\d{1,2}\.\s+", clean):
        return True

    if re.match(r"^\d{1,2}\.\s*$", clean):
        return True

    if "(N. do T.)" in clean or "(N. do T" in clean:
        return True

    return False

#Function to determine if a text block is likely garbage based on its content, category, and common patterns of noise in OCR'd PDFs
def is_garbage_block(text: str, category: str) -> bool:
    clean = text.strip()

    if not clean:
        return True

    # Remove cabeçalhos, rodapés e números de página identificados pela biblioteca
    if category in ["Header", "Footer", "PageNumber"]:
        return True

    # Remove blocos muito curtos sem valor
    if len(clean) <= 2:
        return True

    tokens = clean.split()

    if not tokens:
        return True

    # Detecta textos quebrados em letras soltas:
    single_char_tokens = [token for token in tokens if len(token) == 1]
    single_char_ratio = len(single_char_tokens) / len(tokens)

    if len(tokens) >= 5 and single_char_ratio > 0.45:
        return True

    # Detecta blocos com muitos símbolos/números e pouco texto real
    letters = sum(char.isalpha() for char in clean)
    digits = sum(char.isdigit() for char in clean)
    punctuation = sum(not char.isalnum() and not char.isspace() for char in clean)

    if len(clean) >= 10 and letters < 4 and (digits + punctuation) > letters:
        return True

    # Detecta texto muito espaçado/letra por letra
    spaced_letters_pattern = re.search(r"(?:\b\w\b\s*){5,}", clean)
    if spaced_letters_pattern:
        return True

    return False





#Endpoint to process the uploaded PDF file and return the structured data
@app.post("/process-pdf")
async def process_pdf(file: UploadFile = File(...)):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp:
        temp.write(await file.read())
        temp_path = temp.name

    try:
        elements = partition_pdf(filename=temp_path)

        result = []

        for index, element in enumerate(elements):
            metadata = element.metadata.to_dict() if element.metadata else {}

            #print(element.category, "=>", str(element))

            if element.category == "PageNumber":
                continue

            processed_text = clean_extracted_text(str(element))
            processed_text = remove_trailing_page_number(processed_text, element.category)

            if is_garbage_block(processed_text, element.category):
                continue
            
            if not processed_text:
                continue

            page = metadata.get("page_number")

            if is_page_number_block(processed_text, page):
                continue

            block_type = map_block_type(element.category)

            if is_footnote_block(processed_text):
              block_type = "footnote"

            result.append({
                "order": index + 1,
                "type": block_type,
                "originalText": processed_text,
                "page": page,
                "sourceType": element.category
            })

        return {
            "filename": file.filename,
            "blocks": result
        }

    finally:
        os.remove(temp_path)