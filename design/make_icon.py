"""Draws the Retro-Netplay app icon (ink controller linked to a heat square) for Android and desktop. Needs Pillow."""
import json, math, random
from PIL import Image, ImageDraw, ImageFilter
icons = json.load(open(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), 'icons.json')))
PAPER=(0xE4,0xE3,0xDF); INK=(0x14,0x14,0x14)
STOPS=[(127,208,255),(102,242,176),(232,240,74),(255,147,49),(210,54,46),(122,43,143)]
def spec(t):
    t=max(0,min(1,t))*(len(STOPS)-1); i=min(int(t),len(STOPS)-2); f=t-i
    return tuple(int(STOPS[i][k]+(STOPS[i+1][k]-STOPS[i][k])*f) for k in range(3))
S=1024  # canvas = 108dp adaptive foreground; safe zone ~ inner 66dp
def controller_layer(S, scale, cx, cy, rot=-12):
    m=Image.new('L',(S,S),0); d=ImageDraw.Draw(m)
    a=math.radians(rot); ca,sa=math.cos(a),math.sin(a)
    def tf(p):
        x,y=(p[0]-12)*scale,(p[1]-12)*scale
        return (x*ca-y*sa+cx, x*sa+y*ca+cy)
    d.polygon([tf(p) for p in icons['pad'][0]], fill=255)
    for st in icons['pad'][1:3]:
        d.line([tf(p) for p in st], fill=0, width=int(scale*1.25))
    for st in icons['pad'][1:3]:
        for p in (st[0],st[-1]):
            x,y=tf(p); r=scale*0.62; d.ellipse((x-r,y-r,x+r,y+r),fill=0)
    centers=[]
    for st in icons['pad'][3:]:
        cxx=sum(p[0] for p in st)/len(st); cyy=sum(p[1] for p in st)/len(st)
        x,y=tf((cxx,cyy)); r=scale*1.05; d.ellipse((x-r,y-r,x+r,y+r),fill=0); centers.append((x,y))
    return m, centers
def stipple(mask, seed=3, fade_dir=(0.62,0.55)):
    rnd=random.Random(seed); W=mask.size[0]
    soft=mask.filter(ImageFilter.GaussianBlur(W*0.012))
    out=Image.new('L',(W,W),0); d=ImageDraw.Draw(out)
    core=mask.filter(ImageFilter.MinFilter(int(W*0.02)//2*2+1))
    out.paste(255,(0,0),core)   # solid ink interior for small sizes
    px=soft.load()
    for _ in range(int(W*W*0.09)):
        x=rnd.random()*W; y=rnd.random()*W
        v=px[int(x),int(y)]/255
        if v<=0.01: continue
        fade=max(0,min(1,1.5*(fade_dir[0]*x/W+fade_dir[1]*(1-y/W))-0.3))
        p=v*(0.35+0.65*fade)
        if rnd.random()>p*1.6: continue
        r=W*0.0035*(0.7+rnd.random())
        d.ellipse((x-r,y-r,x+r,y+r),fill=255)
    return out
def heat_square(size, base, seed=1, grid=12, ang=0.7):
    rnd=random.Random(seed); im=Image.new('RGB',(grid,grid))
    ca,sa=math.cos(ang),math.sin(ang)
    for y in range(grid):
        for x in range(grid):
            g=ca*(x/grid-.5)+sa*(y/grid-.5)
            im.putpixel((x,y),spec(base+0.6*g+(rnd.random()-.5)*0.34))
    im=im.resize((size,size),Image.NEAREST)
    return im
def foreground(S, with_color=True):
    fg=Image.new('RGBA',(S,S),(0,0,0,0))
    mask,centers=controller_layer(S, S*0.027, S*0.50, S*0.57, rot=-6)
    ink=stipple(mask)
    # keep the d-pad and buttons crisp: punch them back out after the grain
    holes=Image.new('L',(S,S),0); hd=ImageDraw.Draw(holes)
    full,_=controller_layer(S, S*0.027, S*0.50, S*0.57, rot=-6)
    body=Image.new('L',(S,S),0)
    import math as _m
    a=_m.radians(-6); ca,sa=_m.cos(a),_m.sin(a); sc=S*0.027
    def tf(p):
        x,y=(p[0]-12)*sc,(p[1]-12)*sc
        return (x*ca-y*sa+S*0.50, x*sa+y*ca+S*0.57)
    ImageDraw.Draw(body).polygon([tf(p) for p in icons['pad'][0]],fill=255)
    from PIL import ImageChops
    cut=ImageChops.subtract(body, full)          # exactly the d-pad and button holes
    ink=ImageChops.subtract(ink, cut)
    fg.paste(Image.new('RGBA',(S,S),INK+(255,)),(0,0),ink)
    d=ImageDraw.Draw(fg)
    # the link: a hairline from the right face button out to a heat square (player two)
    sq=int(S*0.15); sx,sy=int(S*0.58),int(S*0.27)
    bx,by=centers[0]
    if with_color:
        d.line((bx,by,sx+sq*0.5,sy+sq*0.5),fill=(244,243,239,255),width=max(2,int(S*0.006)))
        fg.paste(heat_square(sq,0.36,seed=4),(sx,sy))
        d.rectangle((sx,sy,sx+sq-1,sy+sq-1),outline=(244,243,239,255),width=max(2,int(S*0.005)))
        s2=int(S*0.065); tx,ty=int(S*0.27),int(S*0.31)
        d.line((tx+s2*0.5,ty+s2*0.5,S*0.38,S*0.47),fill=INK+(255,),width=max(2,int(S*0.004)))
        fg.paste(heat_square(s2,0.05,seed=9,grid=6),(tx,ty))
    return fg
def _preview():
    bg=Image.new('RGBA',(S,S),PAPER+(255,))
    fg=foreground(S)
    full=Image.alpha_composite(bg,fg)
    full.save('/tmp/icon/full.png')
    # preview at launcher size with a circle mask and squircle
    prev=Image.new('RGB',(560,200),(60,60,60))
    for i,(sz,shape) in enumerate([(192,'circle'),(144,'squircle'),(96,'circle'),(48,'circle')]):
        im=full.resize((sz,sz),Image.LANCZOS)
        mk=Image.new('L',(sz,sz),0); md=ImageDraw.Draw(mk)
        # adaptive: launcher shows the middle 72/108
        crop=full.crop((int(S*18/108),)*2+(int(S*90/108),)*2).resize((sz,sz),Image.LANCZOS)
        if shape=='circle': md.ellipse((0,0,sz-1,sz-1),fill=255)
        else: md.rounded_rectangle((0,0,sz-1,sz-1),radius=sz//4,fill=255)
        prev.paste(crop,(10+sum([202,154,106][:i]),(200-sz)//2),mk)
    prev.save('/tmp/icon/preview.png')

def export(res, desk):
    import os
    from PIL import ImageDraw
    big=1024
    fg=foreground(big); bg=Image.new('RGBA',(big,big),PAPER+(255,))
    full=Image.alpha_composite(bg,fg)
    mono=Image.new('RGBA',(big,big),(0,0,0,0))
    mono.putalpha(fg.split()[3])
    white=Image.new('RGBA',(big,big),(255,255,255,255)); white.putalpha(fg.split()[3]); mono=white
    dens={'mdpi':1,'hdpi':1.5,'xhdpi':2,'xxhdpi':3,'xxxhdpi':4}
    for name,k in dens.items():
        d=os.path.join(res,'mipmap-'+name); os.makedirs(d,exist_ok=True)
        fg.resize((int(108*k),)*2,Image.LANCZOS).save(os.path.join(d,'ic_launcher_foreground.png'))
        mono.resize((int(108*k),)*2,Image.LANCZOS).save(os.path.join(d,'ic_launcher_monochrome.png'))
        crop=full.crop((int(big*16/108),)*2+(int(big*92/108),)*2)
        n=int(48*k); im=crop.resize((n,n),Image.LANCZOS)
        sq=Image.new('L',(n,n),0); ImageDraw.Draw(sq).rounded_rectangle((0,0,n-1,n-1),radius=int(n*0.22),fill=255)
        o=Image.new('RGBA',(n,n),(0,0,0,0)); o.paste(im,(0,0),sq); o.save(os.path.join(d,'ic_launcher.png'))
        rd=Image.new('L',(n,n),0); ImageDraw.Draw(rd).ellipse((0,0,n-1,n-1),fill=255)
        o=Image.new('RGBA',(n,n),(0,0,0,0)); o.paste(im,(0,0),rd); o.save(os.path.join(d,'ic_launcher_round.png'))
    crop=full.crop((int(big*16/108),)*2+(int(big*92/108),)*2)
    for n in (256,):
        im=crop.resize((n,n),Image.LANCZOS)
        sq=Image.new('L',(n,n),0); ImageDraw.Draw(sq).rounded_rectangle((0,0,n-1,n-1),radius=int(n*0.22),fill=255)
        o=Image.new('RGBA',(n,n),(0,0,0,0)); o.paste(im,(0,0),sq); o.save(os.path.join(desk,f'icon-{n}.png'))

if __name__ == '__main__':
    import os
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
    export(os.path.join(root, 'android/app/src/main/res'), os.path.join(root, 'desktop'))
    print('icons written')
