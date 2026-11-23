USE yemekhane;

-- 1. Sütun daha önce eklenmediyse bildirim_tercihi sütununu ekle (MySQL 8.4 uyumlu & idempotent)
SET @sutun_var_mi = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = 'yemekhane'
    AND TABLE_NAME = 'kullanicilar'
    AND COLUMN_NAME = 'bildirim_tercihi'
);

SET @sorgu = IF(@sutun_var_mi = 0,
  'ALTER TABLE kullanicilar ADD COLUMN bildirim_tercihi ENUM(''eposta'', ''sms'', ''kapali'') NOT NULL DEFAULT ''eposta'' AFTER aktif',
  'SELECT ''bildirim_tercihi sutunu zaten mevcut'' AS mesaj'
);

PREPARE stmt FROM @sorgu;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 2. Yemekhane duyuruları için yeni tablo oluşturma
CREATE TABLE IF NOT EXISTS duyurular (
  id INT AUTO_INCREMENT PRIMARY KEY,
  baslik VARCHAR(150) NOT NULL,
  icerik TEXT NOT NULL,
  aktif BOOLEAN NOT NULL DEFAULT TRUE,
  olusturma DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3. Örnek başlangıç duyurusu
INSERT IGNORE INTO duyurular (id, baslik, icerik, aktif)
VALUES (1, 'Haftalık Menü Güncellemesi', 'Öğle ve akşam menüleri her Pazar akşamı sisteme yüklenmektedir.', TRUE);
