# Kampus Yemekhane Otomasyonu

* Ogrenci: Ahmet Yavuz Osmanoglu
* Ogrenci No: 241229073
* Bolum: Yazilim Muhendisligi - Konya Teknik Universitesi

## Kurulum ve Calistirma
Proje klasorunde 'docker compose up -d --build' komutunu calistirin. Tarayicidan http://localhost:8080 adresinden giris yapabilirsiniz (Test sifresi: Yemek2026!).

## Mimari ve Teknik Kararlar
1. Okuma/Yazma Ayrimi ve Read-After-Write Tutarliligi: Rezervasyon olusturma, iptal ve bakiye yukleme islemleri mysql-primary uzerinde atomik sakli yordamlarla (sp_rezervasyon_yap, sp_rezervasyon_iptal, sp_bakiye_yukle, sp_turnike_gecis) yurutulur. Kullaniciya ozel anlik bakiye ve rezervasyon sorgulari (/api/ben, /api/rezervasyonlarim) replikasyon gecikmesinden etkilenmemek icin dogrudan mysql-primary uzerinden okunur.
2. Redis Onbellekleme ve Oturum Yonetimi: Kullanici oturumlari 24 saat TTL ile Redis uzerinde tutulur. Gunluk menuler (/api/menuler) mysql-replica uzerinden okunup 120 saniye Redis'te onbelleklenir; rezervasyon, iptal veya yeni menu ekleme islemlerinde ilgili menuler:* anahtarlari silinerek bayat veri gosterimi engellenir.
3. Veritabani Sema Degisikligi (Migration): db/init/04_degisiklik.sql dosyasi mevcut tablolari koruyarak kullanicilar tablosuna bildirim_tercihi sutununu ve duyurular tablosunu idempotent yapida ekler.
