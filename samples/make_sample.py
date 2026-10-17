from PIL import Image, ImageDraw, ImageFont
import numpy as np
S = 1080
y, x = np.mgrid[0:S, 0:S]
r = (60 + 120 * x / S).astype(np.uint8); g = (90 + 100 * y / S).astype(np.uint8); b = np.full((S, S), 200, np.uint8)
img = Image.fromarray(np.dstack([r, g, b]))
d = ImageDraw.Draw(img)
f1 = ImageFont.truetype("C:/Windows/Fonts/malgunbd.ttf", 96)
f2 = ImageFont.truetype("C:/Windows/Fonts/malgun.ttf", 48)
d.text((80, 120), "겨울 특가", font=f1, fill=(255, 255, 255))
d.text((80, 250), "최대 50% 할인 이벤트", font=f2, fill=(255, 235, 120))
d.ellipse([620, 330, 860, 570], fill=(235, 190, 160))          # 머리
d.rounded_rectangle([560, 560, 920, 1080], 80, fill=(40, 40, 60))  # 몸
d.ellipse([60, 800, 160, 900], fill=(255, 255, 255))           # 장식
img.save("input/sample1.png")
