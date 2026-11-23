import os
import json
import uuid
import bcrypt
import mysql.connector
import redis
from datetime import date
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Response, Cookie
from pydantic import BaseModel

app = FastAPI(title="Yemekhane Otomasyonu API")

def get_db_yazma():
    return mysql.connector.connect(
        host=os.getenv("DB_YAZMA_HOST", "mysql-primary"),
        port=int(os.getenv("DB_PORT", 3306)),
        database=os.getenv("DB_ADI", "yemekhane"),
        user=os.getenv("DB_KULLANICI", "yemekhane_app"),
        password=os.getenv("DB_SIFRE", "Uyg_2026"),
        charset="utf8mb4",
        autocommit=True
    )

def get_db_okuma():
    return mysql.connector.connect(
        host=os.getenv("DB_OKUMA_HOST", "mysql-replica"),
        port=int(os.getenv("DB_PORT", 3306)),
        database=os.getenv("DB_ADI", "yemekhane"),
        user=os.getenv("DB_KULLANICI", "yemekhane_app"),
        password=os.getenv("DB_SIFRE", "Uyg_2026"),
        charset="utf8mb4",
        autocommit=True
    )

def get_redis():
    return redis.Redis(
        host=os.getenv("REDIS_HOST", "redis"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        decode_responses=True
    )

def aktif_kullanici_bul(session_id: Optional[str]):
    if not session_id:
        raise HTTPException(status_code=401, detail="Oturum açmanız gerekiyor.")
    r = get_redis()
    veri = r.get(f"oturum:{session_id}")
    if not veri:
        raise HTTPException(status_code=401, detail="Oturum süresi dolmuş, tekrar giriş yapın.")
    return json.loads(veri)

class GirisIstegi(BaseModel):
    eposta: str
    sifre: str

class RezervasyonIstegi(BaseModel):
    menu_id: int

class BakiyeIstegi(BaseModel):
    tutar: float

class TurnikeIstegi(BaseModel):
    yemekhane_id: int
    zaman: Optional[str] = None

class DegerlendirmeIstegi(BaseModel):
    menu_id: int
    puan: int
    yorum: Optional[str] = ""

class YeniMenuIstegi(BaseModel):
    yemekhane_id: int
    tarih: str
    ogun_id: int
    ogrenci_fiyati: float
    personel_fiyati: float
    yemek_idleri: List[int]

@app.get("/api/")
def saglik():
    return {"durum": "aktif"}

@app.post("/api/giris")
def giris(istek: GirisIstegi, response: Response):
    db = get_db_okuma()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT id, ad, soyad, eposta, sifre_hash, rol, ogrenci_no, bakiye, aktif FROM kullanicilar WHERE eposta = %s", (istek.eposta.strip(),))
    kullanici = cur.fetchone()
    cur.close()
    db.close()

    if not kullanici or not kullanici["aktif"]:
        raise HTTPException(status_code=401, detail="E-posta veya şifre hatalı.")

    if not bcrypt.checkpw(istek.sifre.encode("utf-8"), kullanici["sifre_hash"].encode("utf-8")):
        raise HTTPException(status_code=401, detail="E-posta veya şifre hatalı.")

    oturum_verisi = {
        "id": kullanici["id"],
        "ad": kullanici["ad"],
        "soyad": kullanici["soyad"],
        "eposta": kullanici["eposta"],
        "rol": kullanici["rol"],
        "ogrenci_no": kullanici["ogrenci_no"],
        "bakiye": float(kullanici["bakiye"])
    }
    token = str(uuid.uuid4())
    r = get_redis()
    r.setex(f"oturum:{token}", 86400, json.dumps(oturum_verisi))
    response.set_cookie(key="oturum_id", value=token, httponly=True, samesite="lax", max_age=86400)
    return {"mesaj": "Giriş başarılı", "kullanici": oturum_verisi}

@app.post("/api/cikis")
def cikis(response: Response, oturum_id: Optional[str] = Cookie(default=None)):
    if oturum_id:
        get_redis().delete(f"oturum:{oturum_id}")
    response.delete_cookie("oturum_id")
    return {"mesaj": "Çıkış yapıldı"}

@app.get("/api/ben")
def ben(oturum_id: Optional[str] = Cookie(default=None)):
    kullanici = aktif_kullanici_bul(oturum_id)
    db = get_db_yazma()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT id, ad, soyad, eposta, rol, ogrenci_no, bakiye FROM kullanicilar WHERE id = %s", (kullanici["id"],))
    guncel = cur.fetchone()
    cur.close()
    db.close()
    if guncel:
        guncel["bakiye"] = float(guncel["bakiye"])
        get_redis().setex(f"oturum:{oturum_id}", 86400, json.dumps(guncel))
        return guncel
    return kullanici

@app.get("/api/menuler")
def menuleri_getir(tarih: Optional[str] = None, oturum_id: Optional[str] = Cookie(default=None)):
    aktif_kullanici_bul(oturum_id)
    hedef_tarih = tarih or str(date.today())
    r = get_redis()
    cache_key = f"menuler:{hedef_tarih}"
    onbellek = r.get(cache_key)
    if onbellek:
        return {"kaynak": "redis_cache", "tarih": hedef_tarih, "menuler": json.loads(onbellek)}

    db = get_db_okuma()
    cur = db.cursor(dictionary=True)
    cur.execute("""
        SELECT d.*, dol.kapasite, dol.rezerve, dol.bos_yer, dol.doluluk_yuzde,
               p.oy_sayisi, p.ortalama_puan
        FROM v_menu_detay d
        JOIN v_menu_doluluk dol ON d.menu_id = dol.menu_id
        LEFT JOIN v_menu_puan p ON d.menu_id = p.menu_id
        WHERE d.tarih = %s
        ORDER BY d.yemekhane_id, d.ogun_sira, d.kategori_sira, d.yemek
    """, (hedef_tarih,))
    satirlar = cur.fetchall()
    cur.close()
    db.close()

    menu_map = {}
    for s in satirlar:
        mid = s["menu_id"]
        if mid not in menu_map:
            menu_map[mid] = {
                "menu_id": mid,
                "tarih": str(s["tarih"]),
                "yemekhane_id": s["yemekhane_id"],
                "yemekhane": s["yemekhane"],
                "ogun_id": s["ogun_id"],
                "ogun": s["ogun"],
                "rezervasyon_son": str(s["rezervasyon_son"]) if s["rezervasyon_son"] else None,
                "ogrenci_fiyati": float(s["ogrenci_fiyati"]),
                "personel_fiyati": float(s["personel_fiyati"]),
                "kapasite": s["kapasite"],
                "rezerve": int(s["rezerve"]),
                "bos_yer": int(s["bos_yer"]),
                "doluluk_yuzde": float(s["doluluk_yuzde"] or 0),
                "oy_sayisi": int(s["oy_sayisi"] or 0),
                "ortalama_puan": float(s["ortalama_puan"]) if s["ortalama_puan"] is not None else None,
                "toplam_kalori": 0,
                "yemekler": []
            }
        if s["yemek_id"]:
            kal = int(s["kalori"] or 0)
            menu_map[mid]["toplam_kalori"] += kal
            menu_map[mid]["yemekler"].append({
                "yemek_id": s["yemek_id"],
                "yemek": s["yemek"],
                "kategori": s["kategori"],
                "kalori": kal,
                "vejetaryen": bool(s["vejetaryen"]),
                "alerjenler": s["alerjenler"]
            })

    sonuc = list(menu_map.values())
    r.setex(cache_key, 120, json.dumps(sonuc))
    return {"kaynak": "mysql_replica", "tarih": hedef_tarih, "menuler": sonuc}

@app.get("/api/rezervasyonlarim")
def rezervasyonlarim(oturum_id: Optional[str] = Cookie(default=None)):
    kullanici = aktif_kullanici_bul(oturum_id)
    db = get_db_yazma()
    cur = db.cursor(dictionary=True)
    cur.execute("""
        SELECT r.id, r.menu_id, r.durum, r.ucret, r.olusturma, r.kullanma_zamani,
               m.tarih, y.ad AS yemekhane, m.yemekhane_id, o.ad AS ogun,
               deg.puan AS verdigim_puan, deg.yorum AS verdigim_yorum
        FROM rezervasyonlar r
        JOIN menuler m ON r.menu_id = m.id
        JOIN yemekhaneler y ON m.yemekhane_id = y.id
        JOIN ogunler o ON m.ogun_id = o.id
        LEFT JOIN degerlendirmeler deg ON deg.menu_id = r.menu_id AND deg.kullanici_id = r.kullanici_id
        WHERE r.kullanici_id = %s
        ORDER BY m.tarih DESC, o.id DESC
    """, (kullanici["id"],))
    kayitlar = cur.fetchall()
    cur.close()
    db.close()
    for k in kayitlar:
        k["ucret"] = float(k["ucret"])
        k["tarih"] = str(k["tarih"])
        k["olusturma"] = str(k["olusturma"])
        k["kullanma_zamani"] = str(k["kullanma_zamani"]) if k["kullanma_zamani"] else None
    return {"rezervasyonlar": kayitlar}

@app.post("/api/rezervasyon")
def rezervasyon_yap(istek: RezervasyonIstegi, oturum_id: Optional[str] = Cookie(default=None)):
    kullanici = aktif_kullanici_bul(oturum_id)
    db = get_db_yazma()
    cur = db.cursor(dictionary=True)
    try:
        cur.callproc("sp_rezervasyon_yap", (kullanici["id"], istek.menu_id))
        sonuc = None
        for res in cur.stored_results():
            sonuc = res.fetchone()
        r = get_redis()
        for k in r.keys("menuler:*"):
            r.delete(k)
        return {
            "mesaj": "Rezervasyon oluşturuldu.",
            "rezervasyon_id": sonuc["rezervasyon_id"],
            "ucret": float(sonuc["ucret"]),
            "bakiye": float(sonuc["bakiye"])
        }
    except mysql.connector.Error as err:
        if err.errno == 1644:
            raise HTTPException(status_code=400, detail=err.msg)
        raise HTTPException(status_code=500, detail="Veritabanı işlemi sırasında hata oluştu.")
    finally:
        cur.close()
        db.close()

@app.post("/api/rezervasyon/{rezervasyon_id}/iptal")
def rezervasyon_iptal(rezervasyon_id: int, oturum_id: Optional[str] = Cookie(default=None)):
    kullanici = aktif_kullanici_bul(oturum_id)
    db = get_db_yazma()
    cur = db.cursor(dictionary=True)
    try:
        cur.callproc("sp_rezervasyon_iptal", (kullanici["id"], rezervasyon_id))
        sonuc = None
        for res in cur.stored_results():
            sonuc = res.fetchone()
        r = get_redis()
        for k in r.keys("menuler:*"):
            r.delete(k)
        return {
            "mesaj": "Rezervasyon iptal edildi ve ücret iade edildi.",
            "iade": float(sonuc["iade"]),
            "bakiye": float(sonuc["bakiye"])
        }
    except mysql.connector.Error as err:
        if err.errno == 1644:
            raise HTTPException(status_code=400, detail=err.msg)
        raise HTTPException(status_code=500, detail="İptal işlemi başarısız.")
    finally:
        cur.close()
        db.close()

@app.post("/api/bakiye")
def bakiye_yukle(istek: BakiyeIstegi, oturum_id: Optional[str] = Cookie(default=None)):
    kullanici = aktif_kullanici_bul(oturum_id)
    db = get_db_yazma()
    cur = db.cursor(dictionary=True)
    try:
        cur.callproc("sp_bakiye_yukle", (kullanici["id"], istek.tutar))
        sonuc = None
        for res in cur.stored_results():
            sonuc = res.fetchone()
        return {
            "mesaj": f"{istek.tutar:.2f} TL bakiye yüklendi.",
            "bakiye": float(sonuc["bakiye"])
        }
    except mysql.connector.Error as err:
        if err.errno == 1644:
            raise HTTPException(status_code=400, detail=err.msg)
        raise HTTPException(status_code=500, detail="Bakiye yükleme başarısız.")
    finally:
        cur.close()
        db.close()

@app.post("/api/turnike")
def turnike_gecis(istek: TurnikeIstegi, oturum_id: Optional[str] = Cookie(default=None)):
    kullanici = aktif_kullanici_bul(oturum_id)
    db = get_db_yazma()
    cur = db.cursor(dictionary=True)
    try:
        zaman_arg = istek.zaman if istek.zaman else None
        cur.callproc("sp_turnike_gecis", (kullanici["id"], istek.yemekhane_id, zaman_arg))
        sonuc = None
        for res in cur.stored_results():
            sonuc = res.fetchone()
        return {
            "mesaj": f"Turnike geçişi onaylandı ({sonuc['ogun']}). Afiyet olsun!",
            "rezervasyon_id": sonuc["rezervasyon_id"]
        }
    except mysql.connector.Error as err:
        if err.errno == 1644:
            raise HTTPException(status_code=400, detail=err.msg)
        raise HTTPException(status_code=500, detail="Turnike geçişi başarısız.")
    finally:
        cur.close()
        db.close()

@app.post("/api/degerlendirme")
def degerlendirme_yap(istek: DegerlendirmeIstegi, oturum_id: Optional[str] = Cookie(default=None)):
    kullanici = aktif_kullanici_bul(oturum_id)
    if istek.puan < 1 or istek.puan > 5:
        raise HTTPException(status_code=400, detail="Puan 1 ile 5 arasında olmalıdır.")
    db = get_db_yazma()
    cur = db.cursor(dictionary=True)
    try:
        cur.execute("SELECT id FROM rezervasyonlar WHERE kullanici_id = %s AND menu_id = %s AND durum = 'kullanildi'", (kullanici["id"], istek.menu_id))
        if not cur.fetchone():
            raise HTTPException(status_code=400, detail="Yalnızca turnikeden geçip kullandığınız menüleri değerlendirebilirsiniz.")

        cur.execute("""
            INSERT INTO degerlendirmeler (kullanici_id, menu_id, puan, yorum)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE puan = VALUES(puan), yorum = VALUES(yorum)
        """, (kullanici["id"], istek.menu_id, istek.puan, istek.yorum.strip() or None))

        r = get_redis()
        for k in r.keys("menuler:*"):
            r.delete(k)
        return {"mesaj": "Değerlendirmeniz kaydedildi."}
    finally:
        cur.close()
        db.close()

@app.get("/api/yonetim/secenekler")
def yonetim_secenekler(oturum_id: Optional[str] = Cookie(default=None)):
    kullanici = aktif_kullanici_bul(oturum_id)
    if kullanici["rol"] != "yonetici":
        raise HTTPException(status_code=403, detail="Bu işlem için yönetici yetkisi gerekir.")
    db = get_db_okuma()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT id, ad, kapasite FROM yemekhaneler")
    yemekhaneler = cur.fetchall()
    cur.execute("SELECT id, ad FROM ogunler ORDER BY id")
    ogunler = cur.fetchall()
    cur.execute("SELECT y.id, y.ad, k.ad AS kategori, y.kalori FROM yemekler y JOIN yemek_kategorileri k ON y.kategori_id = k.id ORDER BY k.sira, y.ad")
    yemekler = cur.fetchall()
    cur.close()
    db.close()
    return {"yemekhaneler": yemekhaneler, "ogunler": ogunler, "yemekler": yemekler}

@app.post("/api/yonetim/menu")
def yeni_menu_ekle(istek: YeniMenuIstegi, oturum_id: Optional[str] = Cookie(default=None)):
    kullanici = aktif_kullanici_bul(oturum_id)
    if kullanici["rol"] != "yonetici":
        raise HTTPException(status_code=403, detail="Bu işlem için yönetici yetkisi gerekir.")
    if not istek.yemek_idleri:
        raise HTTPException(status_code=400, detail="En az bir yemek seçmelisiniz.")

    db = get_db_yazma()
    cur = db.cursor()
    try:
        cur.execute("""
            INSERT INTO menuler (yemekhane_id, tarih, ogun_id, ogrenci_fiyati, personel_fiyati)
            VALUES (%s, %s, %s, %s, %s)
        """, (istek.yemekhane_id, istek.tarih, istek.ogun_id, istek.ogrenci_fiyati, istek.personel_fiyati))
        menu_id = cur.lastrowid
        for yid in istek.yemek_idleri:
            cur.execute("INSERT INTO menu_yemekleri (menu_id, yemek_id) VALUES (%s, %s)", (menu_id, yid))
        get_redis().delete(f"menuler:{istek.tarih}")
        return {"mesaj": "Yeni menü başarıyla yayınlandı.", "menu_id": menu_id}
    except mysql.connector.Error as err:
        raise HTTPException(status_code=400, detail=f"Menü eklenemedi: {err.msg}")
    finally:
        cur.close()
        db.close()
