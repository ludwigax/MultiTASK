import os
import os.path as osp
import re
import pdfplumber
import numpy as np

from typing import List, Tuple, Union

from pdfminer.high_level import extract_text, extract_pages
from pdfminer.layout import LAParams
from pdfminer.layout import LTTextBoxHorizontal, LTTextLineHorizontal, LTPage, LTChar, \
    LTLine,LTFigure, LTImage, LTRect

import warnings


r"""
PDFPLUMBER with up-left coordinate system (bbox: x0, y0, x1, y1)

PDFMINER with down-left coordinate system

while bbox is always x1 > x0, y1 > y0,
 use `change_coordinates` to convert the coordinates,
 use `debug_image` to show the image with bounding boxes

"""

def find_mode_value(seq, offset: float = 3.0):
        import numpy as np
        seq = np.array(seq)
        seq = np.sort(seq)
        common = np.zeros_like(seq, dtype=np.int32)
        for i in range(seq.size - 1):
            boolseq = (seq < seq[i] + offset) & (seq > seq[i] - offset) 
            common[i] = boolseq.sum()
        idx = np.argmax(common)
        return seq[idx]

def check_column(lapage: LTPage, offset: float = 3.0):
    boxes_width = []
    for labox in iter(lapage):
        if not isinstance(labox, LTTextBoxHorizontal):
            continue
        if labox.width * labox.height > 10000: 
            boxes_width.append(labox.width)
    if len(boxes_width) == 0:
        return 0
    mode_width = find_mode_value(boxes_width, offset)
    if mode_width < 0.5 * lapage.width:
        return 2
    else:
        return 1
    
def check_columns(lapages: List[LTPage], threshold: float = 0.75, offset: float = 3.0):
    columns = []
    for lapage in lapages:
        column = check_column(lapage, offset)
        if column > 0:
            columns.append(column)
    if len(columns) == 0:
        warnings.warn("No columns detected in the pages, assume single column.")
        return 1
    if sum(columns) / len(columns) > threshold * 2:
        return 2
    else:
        return 1
    
def check_box(labox: LTTextBoxHorizontal, layout, offset: float = 3.0):
    if not layout:
        return True
    x0, y0, x1, y1 = layout
    x0, y0, x1, y1 = x0 - offset, y0 - offset, x1 + offset, y1 + offset
    if labox.x0 > x0 and labox.y0 > y0 and labox.x1 < x1 and labox.y1 < y1:
        return True
    else:
        return False
    
def detect_fonts(lapages: List[LTPage]) -> Tuple[dict, dict]:
    fonts_name = {}
    fonts_size = {}
    for lapage in lapages:
        for labox in iter(lapage):
            if not isinstance(labox, LTTextBoxHorizontal):
                continue
            if not labox.width * labox.height > 10000: 
                continue
            for laline in labox:
                if not isinstance(laline, LTTextLineHorizontal):
                    continue
                for lachar in laline:
                    if not isinstance(lachar, LTChar):
                        continue
                    font = lachar.fontname
                    size = lachar.size
                    fonts_name.setdefault(font, 0)
                    fonts_name[font] += 1
                    size = round(size, 1)
                    fonts_size.setdefault(size, 0)
                    fonts_size[size] += 1        
    return fonts_name, fonts_size

def detect_fonts_line(laline):
    fonts_name = {}
    fonts_size = {}
    for lachar in laline:
        if not isinstance(lachar, LTChar):
            continue
        font = lachar.fontname
        size = lachar.size
        fonts_name.setdefault(font, 0)
        fonts_name[font] += 1
        size = round(size, 1)
        fonts_size.setdefault(size, 0)
        fonts_size[size] += 1        
    return fonts_name, fonts_size
    
def detect_layout(lapage, offset: float = 3.0):
    boxes = []
    for labox in iter(lapage):
        if not isinstance(labox, LTTextBoxHorizontal):
            continue
        if labox.width * labox.height > 10000:
            boxes.append(labox.bbox)
    if len(boxes) == 0:
        return None
    else:
        boxes = np.array(boxes)
        x0, y0 = np.min(boxes[:, 0: 2], axis=0)
        x1, y1 = np.max(boxes[:, 2: 4], axis=0)
        return (x0, y0, x1, y1)
    
def detect_layouts(lapages: List[LTPage], offset: float = 3.0) -> Tuple[Tuple, List[Tuple]]:
    layouts = []
    for lapage in lapages:
        layout = detect_layout(lapage, offset)
        layouts.append(layout)
    common_layout = np.array([la for la in layouts if la is not None])
    if common_layout.size == 0:
        return None, layouts
    else:
        x0, y0 = np.min(common_layout[:, 0: 2], axis=0)
        x1, y1 = np.max(common_layout[:, 2: 4], axis=0)
        common_layout = (x0, y0, x1, y1)
        return common_layout, layouts

class Formattor:
    laparams = LAParams(detect_vertical=True, line_margin=0.4, line_overlap=0.3, char_margin=2.7)
    section_pattern = re.compile(r'^\d+(\.\d+)*\.[ \t]+\S+')
    ref_pattern = re.compile(r"REFERENCES|References")
    ref_2_pattern = re.compile(r"^## REFERENCES|^## References", re.MULTILINE)
    # ref_2_pattern = re.compile(r"^## References\s*", re.MULTILINE)
    kwd_pattern = re.compile(r"^Keywords\s*")

    def __init__(
        self, pdf_path,
        output_name: str = None,
        pages: int | Union[List[int], Tuple[int]] = None, # from 0 to n-1
    ):
        self.pdf_path = pdf_path
        if not output_name:
            output_name = osp.basename(pdf_path).replace('.pdf', '.txt')
        self.output_name = output_name

        self.pdf_obj = None
        if isinstance(pages, int):
            self.pages = [pages]
        elif pages == None:
            self.pages = None
        else:
            self.pages = list(pages)

    def __enter__(self):
        self.open()
        return self
    
    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return self

    def open(self):
        self.pdf_obj = pdfplumber.open(self.pdf_path)
        if self.pages == None:
            self.pages = list(range(len(self.pdf_obj.pages)))
        return self
    
    def close(self):
        self.pdf_obj.close()
        return self
    
    # def extract_text(self):
    #     if not self.pdf_obj:
    #         self.open()

    #     # params = LAParams(detect_vertical=True, line_margin=0.5, line_overlap=0.3, char_margin=2.0) deprecated
    #     # params = LAParams(detect_vertical=True, line_margin=0.4, line_overlap=0.3, char_margin=2.7)
    #     # self.text = extract_text(self.pdf_path, laparams=params)
    #     # self.lines = self.text.split("\n")
    #     self.lapages = extract_pages(self.pdf_path, laparams=self.laparams, page_numbers=self.pages)
    #     return self.lapages
    
    def extract_pages(self) -> List[LTPage]:
        if not self.pdf_obj:
            self.open()

        self.lapages = list(extract_pages(self.pdf_path, laparams=Formattor.laparams, page_numbers=self.pages))
        return self.lapages
    
    def analyze_pages(self):
        self.column = check_columns(self.lapages)
        self.colayout, self.layouts = detect_layouts(self.lapages)
        self.cowidth = self.colayout[2] - self.colayout[0]
        self.coheight = self.colayout[3] - self.colayout[1]
        fonts, size = detect_fonts(self.lapages[1: 3])
        self.cofont = max(fonts, key=fonts.get)
        self.cosize = max(size, key=size.get)
    
    def page_filter(self, page: LTPage, index: int = None, ref: bool = False):
        page_text = ""
        for labox in iter(page):
            if not isinstance(labox, LTTextBoxHorizontal):
                continue
            if labox.width * labox.height < 150:
                continue

            if not check_box(labox, self.colayout):
                continue
            if index == 0 and not check_box(labox, self.layouts[0]):
                continue

            if re.match(Formattor.ref_pattern, labox.get_text()):
                ref = True
                page_text += "## " + labox.get_text() + "\n"
                continue

            if re.match(Formattor.kwd_pattern, labox.get_text()):
                page_text += "**" + labox.get_text() + "\n"
                continue

            if not ref and (self.cowidth / self.column - labox.width) / (self.cowidth / self.column) > 0.35:
                if re.match(Formattor.section_pattern, labox.get_text()):
                    page_text += "**" + labox.get_text() + "\n"
                    continue
                else:
                    laline = list(labox)[0]
                    _, size = detect_fonts_line(laline)
                    if max(size, key=size.get) - self.cosize >= 2:
                        page_text += "**" + laline.get_text() + "\n"
                        continue
                    else:
                        continue
                        # labox = list(labox)[1:]

            if (labox.width * labox.height) / len(list(labox)) < 500:
                continue

            for laline in labox:
                if not isinstance(laline, LTTextLineHorizontal):
                    continue
                if laline.width * laline.height < 150:
                    continue
                page_text += laline.get_text()
            page_text += "\n"
        return page_text, ref
    
    def text(self, save: bool = False, no_ref: bool = False):
        main_text = ""
        refers = False
        for i, page in enumerate(self.lapages):
            page_text, refers = self.page_filter(page, i, refers)
            main_text += page_text + "\n--\n\n"
        
        if no_ref:
            match = re.search(Formattor.ref_2_pattern, main_text)
            if match:
                index = match.start()
                main_text = main_text[:index]
                
        if save:
            with open(self.output_name, "w", encoding="utf-8") as f:
                f.write(main_text)
        return main_text
    
    def debug_image(self, page_index: int = 0, boxes: List[Tuple] = None):
        lapages = getattr(self, "lapages", None)
        if not lapages:
            self.extract_pages()
            lapages = self.lapages

        if not boxes:
            boxes = []

        if not self.pdf_obj:
            self.open()
        im = self.pdf_obj.pages[page_index].to_image()

        lapage = lapages[page_index]
        for labox in iter(lapage):
            bbox = change_coordinates(labox.bbox, lapage.width, lapage.height)
            if isinstance(labox, LTTextBoxHorizontal):
                colors = (255, 0, 0)
            elif isinstance(labox, LTLine):
                colors = (0, 0, 255)
            elif isinstance(labox, LTRect):
                colors = (0, 255, 0)
            elif isinstance(labox, LTFigure):
                colors = (255, 255, 0)
            else:
                print(labox)
                colors = (0, 255, 255)
            im.draw_rect(bbox, stroke=colors, stroke_width=2)
        im.show()
        return im, lapages[0]
    
    def debug_text(self, page_index: int = 0) -> str:
        lapages = getattr(self, "lapages", None)
        if not lapages:
            self.extract_pages()
            lapages = self.lapages
        
        return lapages[page_index], lapages[page_index]

# utils -----------------------------------------------------------------------
def find_files(dir, pattern):
    file_paths = []
    for file in os.listdir(dir):
        if osp.basename(file).endswith(pattern):
            file_paths.append(osp.join(dir, file))
    return file_paths

def change_coordinates(bbox: Tuple, width: int, height: int) -> Tuple:
    x0, y0, x1, y1 = bbox
    y0 = height - y0
    y1 = height - y1
    return (x0, y1, x1, y0)

# def easy_use(pdf_path: str) -> List[str]:
#     formattor = Formattor(pdf_path)
#     formattor.extract_pages()
#     formattor.analyze_pages()
#     text = formattor.text(save=False, no_ref=True)
#     formattor.close()
#     text = text.replace("\n\n--\n\n", "")
#     paragraphs = text.split("\n\n")
#     new_paragraphs = []
#     for para in paragraphs:
#         space_pattern = re.compile(r"\s*\n")
#         para = re.sub(space_pattern, " ", para)
#         new_paragraphs.append(para)
#     return new_paragraphs

def extract_pdf_text(path: str, output_path: str= None, save: bool = False, no_ref: bool = True) -> str:
    formattor = Formattor(path, output_name=output_path)
    formattor.extract_pages()
    formattor.analyze_pages()
    text = formattor.text(save=save, no_ref=no_ref)
    formattor.close()
    return text

def extract_pdf_text_coarsed(path: str, output_path: str=None) -> str:
    text = extract_text(path)
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text)
    return text