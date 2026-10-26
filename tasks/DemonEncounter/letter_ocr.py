"""Read quiz options, including isolated digits missed by text detection."""
import re

import cv2


def read_letter_option(image, rule):
    text = rule.detect_text(image)
    if text.strip():
        return text

    # Options are centered. Exclude the button border and recognize the line
    # directly: the text detector can discard a single 6/8/9 altogether.
    x, y, width, height = rule.roi
    center = x + width // 2
    crop = image[y + 12:y + height - 12, center - 90:center + 90]
    if not crop.size:
        return ''
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    binary = cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY)[1]
    binary = cv2.copyMakeBorder(binary, 8, 8, 8, 8, cv2.BORDER_CONSTANT, value=255)
    value, score = rule.model.ocr_single_line(binary)
    value = value.strip()
    return value if score >= rule.score and re.fullmatch(r'[0-9]+', value) else ''
