import json, sys
sys.path.insert(0, ".")
from ima_ocr import IMAController

cfg = json.load(open("config.json", encoding="utf-8"))["ima"]
ima = IMAController(cfg)
if not ima.find_window():
    print("FIND_WINDOW_FAIL")
    raise SystemExit
ima._restore()
img, _ = ima.capture()
from ocr_engine import ocr_image
print("IMA WINDOW OCR (%d blocks):" % len(ocr_image(img)))
for t, x, y in ocr_image(img):
    print("  ", repr(t), "x=%d y=%d" % (x, y))
