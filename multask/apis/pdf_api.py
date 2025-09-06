"""
PDF processing interface for document text extraction.

This module provides a high-level interface for processing PDF documents
using the multask execution framework with proper error handling.
"""

import os
import os.path as osp
from typing import List, Dict, Any, Optional, Union, Tuple

# Check for required dependencies with detailed error messages
try:
    import pdfplumber
    from pdfminer.high_level import extract_text, extract_pages
    from pdfminer.layout import LAParams, LTTextBoxHorizontal, LTTextLineHorizontal, LTPage, LTChar
    PDF_AVAILABLE = True
except ImportError as e:
    PDF_AVAILABLE = False
    _pdf_import_error = str(e)
    # Create dummy classes to avoid NameError
    class LAParams: pass
    class LTPage: pass
    class LTTextBoxHorizontal: pass
    class LTTextLineHorizontal: pass
    class LTChar: pass

if not PDF_AVAILABLE:
    raise ImportError(
        f"PDF processing dependencies are missing: {_pdf_import_error}\n"
        "Install with: pip install pdfplumber pdfminer.six"
    )

from ..core import ThreadExecutor
from ..core.controllers import BasicController, SmartController, BasicControllerConfig, SmartControllerConfig
from ..core.rate_limiter import RateLimitConfig
from ..core.exceptions import FatalError
import warnings
import re
import numpy as np


def pdf_extract_worker(
    pdf_path: str,
    output_path: Optional[str] = None,
    method: str = "advanced",
    pages: Optional[Union[int, List[int], Tuple[int]]] = None,
    no_references: bool = True,
    **kwargs
) -> Dict[str, Any]:
    """
    Worker function for extracting text from a single PDF.
    
    Args:
        pdf_path: Path to the PDF file
        output_path: Optional path to save extracted text
        method: Extraction method ('simple' or 'advanced')
        pages: Specific pages to extract (None for all)
        no_references: Whether to remove references section
        **kwargs: Additional parameters
        
    Returns:
        Dict containing extracted text and metadata
        
    Raises:
        FatalError: If PDF file is not found or invalid
    """
    if not os.path.exists(pdf_path):
        raise FatalError(f"PDF file not found: {pdf_path}")
    
    try:
        if method == "simple":
            text = extract_text(pdf_path)
        else:  # advanced method
            text = _advanced_extract_text(pdf_path, output_path, pages, no_references)
        
        # Save to file if requested
        if output_path:
            try:
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(text)
            except Exception as e:
                # Don't fail the whole extraction for save errors
                pass
        
        return {
            "pdf_path": pdf_path,
            "text": text,
            "method": method,
            "character_count": len(text),
            "word_count": len(text.split()),
            "page_count": _get_page_count(pdf_path),
            "success": True
        }
        
    except Exception as e:
        return {
            "pdf_path": pdf_path,
            "error": str(e),
            "success": False
        }


def _get_page_count(pdf_path: str) -> int:
    """Get the number of pages in a PDF."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            return len(pdf.pages)
    except Exception:
        return 0


def _advanced_extract_text(
    pdf_path: str, 
    output_path: Optional[str] = None,
    pages: Optional[Union[int, List[int], Tuple[int]]] = None,
    no_references: bool = True
) -> str:
    """Advanced PDF text extraction with layout analysis."""
    
    class PDFFormatter:
        """Advanced PDF text formatter with layout analysis."""
        
        laparams = LAParams(detect_vertical=True, line_margin=0.4, line_overlap=0.3, char_margin=2.7)
        section_pattern = re.compile(r'^\d+(\.\d+)*\.[ \t]+\S+')
        ref_pattern = re.compile(r"REFERENCES|References")
        ref_2_pattern = re.compile(r"^## REFERENCES|^## References", re.MULTILINE)
        kwd_pattern = re.compile(r"^Keywords\s*")
        
        def __init__(self, pdf_path: str, pages: Optional[Union[int, List[int], Tuple[int]]] = None):
            self.pdf_path = pdf_path
            self.pdf_obj = None
            
            if isinstance(pages, int):
                self.pages = [pages]
            elif pages is None:
                self.pages = None
            else:
                self.pages = list(pages)
        
        def __enter__(self):
            self.open()
            return self
        
        def __exit__(self, exc_type, exc_value, traceback):
            self.close()
        
        def open(self):
            self.pdf_obj = pdfplumber.open(self.pdf_path)
            if self.pages is None:
                self.pages = list(range(len(self.pdf_obj.pages)))
            return self
        
        def close(self):
            if self.pdf_obj:
                self.pdf_obj.close()
        
        def extract_pages(self) -> List[LTPage]:
            """Extract pages using pdfminer."""
            self.lapages = list(extract_pages(
                self.pdf_path, 
                laparams=self.laparams, 
                page_numbers=self.pages
            ))
            return self.lapages
        
        def analyze_pages(self):
            """Analyze page layout and fonts."""
            self.column = self._check_columns(self.lapages)
            self.colayout, self.layouts = self._detect_layouts(self.lapages)
            if self.colayout:
                self.cowidth = self.colayout[2] - self.colayout[0]
                self.coheight = self.colayout[3] - self.colayout[1]
            else:
                self.cowidth = self.coheight = 0
            
            # Analyze fonts from first few pages
            fonts, sizes = self._detect_fonts(self.lapages[1:3] if len(self.lapages) > 1 else self.lapages)
            self.cofont = max(fonts, key=fonts.get) if fonts else ""
            self.cosize = max(sizes, key=sizes.get) if sizes else 12
        
        def extract_text(self, no_references: bool = True) -> str:
            """Extract and format text from all pages."""
            main_text = ""
            refers = False
            
            for i, page in enumerate(self.lapages):
                page_text, refers = self._process_page(page, i, refers)
                main_text += page_text + "\n--\n\n"
            
            if no_references:
                match = re.search(self.ref_2_pattern, main_text)
                if match:
                    main_text = main_text[:match.start()]
            
            return main_text
        
        def _check_columns(self, lapages: List[LTPage], threshold: float = 0.75) -> int:
            """Detect if document has single or double columns."""
            columns = []
            for lapage in lapages:
                boxes_width = []
                for labox in iter(lapage):
                    if isinstance(labox, LTTextBoxHorizontal) and labox.width * labox.height > 10000:
                        boxes_width.append(labox.width)
                
                if boxes_width:
                    mode_width = self._find_mode_value(boxes_width)
                    if mode_width < 0.5 * lapage.width:
                        columns.append(2)
                    else:
                        columns.append(1)
            
            if not columns:
                return 1
            
            return 2 if sum(columns) / len(columns) > threshold * 2 else 1
        
        def _find_mode_value(self, seq: List[float], offset: float = 3.0) -> float:
            """Find the most common value in a sequence."""
            if not seq:
                return 0
            seq = np.array(seq)
            seq = np.sort(seq)
            common = np.zeros_like(seq, dtype=np.int32)
            for i in range(seq.size - 1):
                boolseq = (seq < seq[i] + offset) & (seq > seq[i] - offset)
                common[i] = boolseq.sum()
            idx = np.argmax(common)
            return seq[idx]
        
        def _detect_layouts(self, lapages: List[LTPage]) -> Tuple[Optional[Tuple], List[Optional[Tuple]]]:
            """Detect common layout across pages."""
            layouts = []
            for lapage in lapages:
                boxes = []
                for labox in iter(lapage):
                    if isinstance(labox, LTTextBoxHorizontal) and labox.width * labox.height > 10000:
                        boxes.append(labox.bbox)
                
                if boxes:
                    boxes = np.array(boxes)
                    x0, y0 = np.min(boxes[:, 0:2], axis=0)
                    x1, y1 = np.max(boxes[:, 2:4], axis=0)
                    layouts.append((x0, y0, x1, y1))
                else:
                    layouts.append(None)
            
            valid_layouts = [la for la in layouts if la is not None]
            if not valid_layouts:
                return None, layouts
            
            common_layout = np.array(valid_layouts)
            x0, y0 = np.min(common_layout[:, 0:2], axis=0)
            x1, y1 = np.max(common_layout[:, 2:4], axis=0)
            return (x0, y0, x1, y1), layouts
        
        def _detect_fonts(self, lapages: List[LTPage]) -> Tuple[Dict[str, int], Dict[float, int]]:
            """Detect common fonts and sizes."""
            fonts_name = {}
            fonts_size = {}
            
            for lapage in lapages:
                for labox in iter(lapage):
                    if not isinstance(labox, LTTextBoxHorizontal) or labox.width * labox.height <= 10000:
                        continue
                    
                    for laline in labox:
                        if not isinstance(laline, LTTextLineHorizontal):
                            continue
                        
                        for lachar in laline:
                            if not isinstance(lachar, LTChar):
                                continue
                            
                            font = lachar.fontname
                            size = round(lachar.size, 1)
                            
                            fonts_name.setdefault(font, 0)
                            fonts_name[font] += 1
                            fonts_size.setdefault(size, 0)
                            fonts_size[size] += 1
            
            return fonts_name, fonts_size
        
        def _process_page(self, page: LTPage, index: int, ref: bool) -> Tuple[str, bool]:
            """Process a single page and extract formatted text."""
            page_text = ""
            
            for labox in iter(page):
                if not isinstance(labox, LTTextBoxHorizontal):
                    continue
                if labox.width * labox.height < 150:
                    continue
                
                # Check if box is within main layout
                if self.colayout and not self._check_box_in_layout(labox, self.colayout):
                    continue
                if index == 0 and self.layouts[0] and not self._check_box_in_layout(labox, self.layouts[0]):
                    continue
                
                box_text = labox.get_text()
                
                # Check for references section
                if re.match(self.ref_pattern, box_text):
                    ref = True
                    page_text += "## " + box_text + "\n"
                    continue
                
                # Check for keywords
                if re.match(self.kwd_pattern, box_text):
                    page_text += "**" + box_text + "\n"
                    continue
                
                # Check for section headers (if not in references)
                if not ref and self.column and self.cowidth:
                    width_ratio = (self.cowidth / self.column - labox.width) / (self.cowidth / self.column)
                    if width_ratio > 0.35:
                        if re.match(self.section_pattern, box_text):
                            page_text += "**" + box_text + "\n"
                            continue
                        else:
                            # Check font size for headers
                            lines = list(labox)
                            if lines:
                                first_line = lines[0]
                                if isinstance(first_line, LTTextLineHorizontal):
                                    _, sizes = self._detect_fonts_line(first_line)
                                    if sizes and max(sizes, key=sizes.get) - self.cosize >= 2:
                                        page_text += "**" + first_line.get_text() + "\n"
                                        continue
                
                # Regular text processing
                if (labox.width * labox.height) / len(list(labox)) >= 500:
                    for laline in labox:
                        if isinstance(laline, LTTextLineHorizontal) and laline.width * laline.height >= 150:
                            page_text += laline.get_text()
                    page_text += "\n"
            
            return page_text, ref
        
        def _check_box_in_layout(self, labox: LTTextBoxHorizontal, layout: Tuple, offset: float = 3.0) -> bool:
            """Check if a text box is within the given layout."""
            if not layout:
                return True
            x0, y0, x1, y1 = layout
            x0, y0, x1, y1 = x0 - offset, y0 - offset, x1 + offset, y1 + offset
            return (labox.x0 > x0 and labox.y0 > y0 and 
                   labox.x1 < x1 and labox.y1 < y1)
        
        def _detect_fonts_line(self, laline: LTTextLineHorizontal) -> Tuple[Dict[str, int], Dict[float, int]]:
            """Detect fonts in a single line."""
            fonts_name = {}
            fonts_size = {}
            
            for lachar in laline:
                if not isinstance(lachar, LTChar):
                    continue
                
                font = lachar.fontname
                size = round(lachar.size, 1)
                
                fonts_name.setdefault(font, 0)
                fonts_name[font] += 1
                fonts_size.setdefault(size, 0)
                fonts_size[size] += 1
            
            return fonts_name, fonts_size
    
    # Use the formatter to extract text
    with PDFFormatter(pdf_path, pages) as formatter:
        formatter.extract_pages()
        formatter.analyze_pages()
        return formatter.extract_text(no_references)


class PDFExecutor(ThreadExecutor):
    """
    Specialized executor for PDF processing tasks.
    
    Features:
    - Thread-based processing (PDF libraries are not async)
    - Built-in error handling for common PDF issues
    - Support for both simple and advanced text extraction
    """
    
    def __init__(self, **kwargs):
        """
        Initialize PDF executor.
        
        Args:
            **kwargs: Additional executor parameters
        """
        # Set up default controller configuration
        _setup_pdf_controller_config(kwargs)
        
        super().__init__(
            worker=pdf_extract_worker,
            **kwargs
        )


def pdf_batch_extract(
    pdf_paths: List[str],
    output_dir: Optional[str] = None,
    method: str = "advanced",
    max_workers: int = 3,
    no_references: bool = True,
    controller_type: str = "basic",
    **executor_kwargs
) -> List[Dict[str, Any]]:
    """
    High-level function for batch PDF text extraction.
    
    Args:
        pdf_paths: List of paths to PDF files
        output_dir: Optional directory to save extracted text files
        method: Extraction method ('simple' or 'advanced')
        max_workers: Maximum concurrent workers
        no_references: Whether to remove references section
        controller_type: Type of controller to use ("basic" or "smart")
        **executor_kwargs: Additional executor parameters
        
    Returns:
        List of extraction result dictionaries
        
    Example:
        pdf_files = ["doc1.pdf", "doc2.pdf", "doc3.pdf"]
        results = await pdf_batch_extract(pdf_files, output_dir="extracted_texts")
    """
    # Prepare tasks
    tasks = []
    for pdf_path in pdf_paths:
        task = {
            "pdf_path": pdf_path,
            "method": method,
            "no_references": no_references
        }
        
        if output_dir:
            # Generate output path
            base_name = osp.splitext(osp.basename(pdf_path))[0]
            output_path = osp.join(output_dir, f"{base_name}.txt")
            task["output_path"] = output_path
        
        tasks.append(task)
    
    # Create executor
    executor = PDFExecutor(
        max_workers=max_workers,
        controller_type=controller_type,
        **executor_kwargs
    )
    
    # Execute tasks - PDF executor is already synchronous
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    if loop.is_running():
        # If we're already in an async context, use a thread pool
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as thread_executor:
            future = thread_executor.submit(lambda: asyncio.run(executor.execute(tasks)))
            return future.result()
    else:
        return loop.run_until_complete(executor.execute(tasks))


def _setup_pdf_controller_config(kwargs: dict) -> None:
    """Set up default controller configuration for PDF processing."""
    controller_type = kwargs.pop('controller_type', 'basic')  # Default to basic for PDF processing
    
    if controller_type == 'smart':
        if 'smart_controller_config' not in kwargs:
            kwargs['smart_controller_config'] = SmartControllerConfig(
                rate_limit_config=RateLimitConfig(max_rpm=1000, safety_factor=0.9)
            )
    else:
        if 'basic_controller_config' not in kwargs:
            kwargs['basic_controller_config'] = BasicControllerConfig(
                rate_limit_config=RateLimitConfig(max_rpm=1000, safety_factor=0.9)
            )
    
    kwargs['controller_type'] = controller_type
