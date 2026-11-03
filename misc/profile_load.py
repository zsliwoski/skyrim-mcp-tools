"""Times loading each record signature of Skyrim.esm on its own."""
import time
from esplib import Plugin

SKYRIM_ESM = r"C:\Program Files (x86)\Steam\steamapps\common\Skyrim Special Edition\Data\Skyrim.esm"

full = Plugin(SKYRIM_ESM)
counts = {sig: len(recs) for sig, recs in full._signature_index.items()}
del full

results = []
for sig, count in counts.items():
    start = time.perf_counter()
    Plugin.load(SKYRIM_ESM, only_signatures={sig})
    results.append((time.perf_counter() - start, sig, count))

results.sort(reverse=True)
print(f"{'sig':<6}{'records':>9}{'seconds':>10}{'ms/record':>11}")
for secs, sig, count in results:
    print(f"{sig:<6}{count:>9}{secs:>10.2f}{secs * 1000 / count:>11.3f}")
print(f"\nsum of parts: {sum(r[0] for r in results):.1f}s")
