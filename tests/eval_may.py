"""Score the extraction engine against the hand-built May 2025 reference."""
import glob, os, re, sys, zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from dotbo.extract import extract          # noqa: E402
from dotbo.pdftext import Document         # noqa: E402
from dotbo.urls import normalise_key       # noqa: E402

REF = "/Users/manrajsingh/Downloads/Example of Blocking Orders Package/Blocking Orders_May"
DOCX = os.path.join(REF, "Blocking orders_May.docx")


def ground_truth():
    xml = zipfile.ZipFile(DOCX).read("word/document.xml").decode("utf-8")
    out, cur = {}, None
    hdr = re.compile(r"^(\d+)_\s*Blocking order dated", re.I)
    for para in re.findall(r"<w:p\b.*?</w:p>", xml, re.S):
        t = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", para, re.S))
        t = t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").strip()
        m = hdr.match(t)
        if m:
            cur = int(m.group(1))
            out[cur] = []
        elif cur and t:
            out[cur].append(t)
    return out


def key(u):
    return normalise_key(u).replace(" ", "")


def main(use_ocr=True):
    gt = ground_truth()
    files = sorted(
        glob.glob(REF + "/*.pdf"),
        key=lambda p: int(re.match(r"(\d+)", os.path.basename(p)).group(1)),
    )
    tp = fp = fn = 0
    bad = []
    for f in files:
        n = int(re.match(r"(\d+)", os.path.basename(f)).group(1))
        exp = {key(u) for u in gt.get(n, [])}
        with Document(f, use_ocr=use_ocr) as doc:
            _letter, ex = extract(doc)
        got = {key(u) for u in ex.urls}
        t, p, m = len(exp & got), len(got - exp), len(exp - got)
        tp, fp, fn = tp + t, fp + p, fn + m
        flag = "" if not (p or m) else "  <<<"
        print(
            f"{n:3d} decl={str(ex.declared_count):>4s} got={len(got):3d} exp={len(exp):3d} "
            f"{ex.confidence:6s} {ex.source:11s} pg={str(ex.pages)[:20]:20s} "
            f"{ex.strategy:6s} +{p} -{m}{flag}"
        )
        if p or m:
            bad.append((n, sorted(got - exp)[:8], sorted(exp - got)[:8]))
    prec = tp / max(tp + fp, 1)
    rec = tp / max(tp + fn, 1)
    print(f"\nTOTAL tp={tp} fp={fp} fn={fn}  P={prec:.4f} R={rec:.4f}")
    for n, e, m in bad:
        print(f"\n#{n} EXTRA={e}\n    MISS={m}")
    return prec, rec


if __name__ == "__main__":
    main(use_ocr="--no-ocr" not in sys.argv)
