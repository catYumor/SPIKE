-- ============================================================
-- SPIKE 数据库 schema
-- 依据《SPIKE 产品技术栈说明与实现方法》第 7 节
-- 注意：本机 WSL 内 MySQL 使用 3307 端口（3306 被 Windows MySQL80 占用）
-- ============================================================

CREATE DATABASE IF NOT EXISTS spike
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE spike;

-- ---------- 7.1 表结构 ----------

CREATE TABLE IF NOT EXISTS products (
  product_id VARCHAR(64) PRIMARY KEY,
  brand VARCHAR(128),
  model VARCHAR(128),
  category VARCHAR(128),
  is_target TINYINT DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS aspect_dictionary (
  aspect_id INT AUTO_INCREMENT PRIMARY KEY,
  aspect_name VARCHAR(128) NOT NULL UNIQUE,
  synonyms TEXT,
  category VARCHAR(128)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS review_aspects (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  review_id VARCHAR(128),
  product_id VARCHAR(64),
  aspect_name VARCHAR(128),
  sentiment ENUM('positive','negative','neutral'),
  -- 评论星级 1-5。规格 §8.4 的 severity 公式需要按星级拆负面提及数，
  -- 但原 DDL 未列此字段，见 docs/实现偏差记录.md D1
  score TINYINT,
  confidence DECIMAL(5,4),
  evidence_text TEXT,
  create_time DATETIME,
  INDEX idx_product_aspect (product_id, aspect_name),
  INDEX idx_review (review_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS aspect_aggregation (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  product_id VARCHAR(64),
  aspect_name VARCHAR(128),
  pos_count INT DEFAULT 0,
  neg_count INT DEFAULT 0,
  neu_count INT DEFAULT 0,
  total_count INT DEFAULT 0,
  pos_rate DECIMAL(6,4),
  neg_rate DECIMAL(6,4),
  UNIQUE KEY uk_product_aspect (product_id, aspect_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS aspect_priority (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  product_id VARCHAR(64),
  aspect_name VARCHAR(128),
  kano_type ENUM('must_be','one_dimensional','attractive','indifferent'),
  priority_score DECIMAL(8,4),
  label ENUM('advantage','painpoint','neutral'),
  updated_at DATETIME,
  UNIQUE KEY uk_product_aspect (product_id, aspect_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS competitive_gap (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  category VARCHAR(128),
  aspect_name VARCHAR(128),
  target_product_id VARCHAR(64),
  competitor_product_id VARCHAR(64),
  target_pos_rate DECIMAL(6,4),
  competitor_pos_rate DECIMAL(6,4),
  pos_gap DECIMAL(7,4),
  target_neg_rate DECIMAL(6,4),
  competitor_neg_rate DECIMAL(6,4),
  neg_gap DECIMAL(7,4),
  updated_at DATETIME,
  UNIQUE KEY uk_gap (aspect_name, target_product_id, competitor_product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------- 应用账号 ----------
-- 密码与 config/settings.py 保持一致
CREATE USER IF NOT EXISTS 'spike'@'localhost' IDENTIFIED BY 'spike_pass_2026';
CREATE USER IF NOT EXISTS 'spike'@'127.0.0.1' IDENTIFIED BY 'spike_pass_2026';
CREATE USER IF NOT EXISTS 'spike'@'%' IDENTIFIED BY 'spike_pass_2026';
GRANT ALL PRIVILEGES ON spike.* TO 'spike'@'localhost';
GRANT ALL PRIVILEGES ON spike.* TO 'spike'@'127.0.0.1';
GRANT ALL PRIVILEGES ON spike.* TO 'spike'@'%';
FLUSH PRIVILEGES;

-- ---------- 种子数据 ----------

-- 产品（对应 config/products.yaml）
-- 2026-11-02：原示例 SKU 100012043978 实测为茅台白酒；
--             100433292168（Redmi K100）为未开售新机无评价。
--             最终改用下列两台，评价量充足。见 docs/实现偏差记录.md D5 / D7
-- 2026-11-04：竞品由 100300314335 华为 nova 16 SE 改为 100224796932 华为 nova 14，
--             因为交付的补充数据是 nova 14，只有它同时有 500 好评 + 200 差评，
--             两边样本结构才对称。见 docs/实现偏差记录.md D11
INSERT INTO products (product_id, brand, model, category, is_target) VALUES
  ('100215674663', '小米', 'REDMI K90', '手机', 1),
  ('100224796932', '华为', 'nova 14',   '手机', 0)
ON DUPLICATE KEY UPDATE
  brand=VALUES(brand), model=VALUES(model), category=VALUES(category), is_target=VALUES(is_target);

-- 清理历次弃用的示例数据
DELETE FROM products WHERE product_id IN
  ('100012043978', '100026667910', '100433292168', '100300314335');

-- 属性字典（对应 config/aspects.yaml 示例）
INSERT INTO aspect_dictionary (aspect_name, synonyms, category) VALUES
  ('电池续航', '电池,续航,电量,待机,充电',            '手机'),
  ('影像拍照', '拍照,相机,像素,成像,夜景',            '手机'),
  ('屏幕显示', '屏幕,显示,刷新率,亮度',              '手机'),
  ('做工质感', '做工,质感,手感,外壳',                '手机'),
  ('物流配送', '物流,快递,配送,发货',                '手机'),
  ('客服服务', '客服,售后,服务',                     '手机'),
  ('性价比',   '价格,性价比,贵,便宜',                '手机')
ON DUPLICATE KEY UPDATE
  synonyms=VALUES(synonyms), category=VALUES(category);
