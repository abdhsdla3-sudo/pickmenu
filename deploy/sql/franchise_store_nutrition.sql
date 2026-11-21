BEGIN;

CREATE OR REPLACE VIEW pickmenu_franchise.public_store_nutrition AS
WITH products AS (
    SELECT store_id, standard_menu_id, brand, nutrition_id, product_name, basis,
           energy_kcal, protein_g, carbohydrate_g, fat_g, sugar_g, sodium_mg,
           bool_and(coalesce(approval_status = '승인', false)) AS approved
    FROM pickmenu_franchise.store_candidates
    WHERE sha256 = (
        SELECT sha256 FROM pickmenu_franchise.workbooks
        ORDER BY imported_at DESC, sha256 DESC LIMIT 1
    )
    GROUP BY store_id, standard_menu_id, brand, nutrition_id, product_name, basis,
             energy_kcal, protein_g, carbohydrate_g, fat_g, sugar_g, sodium_mg
), counted AS (
    SELECT *, count(*) OVER (PARTITION BY store_id, standard_menu_id) AS product_count
    FROM products
)
SELECT store_id, standard_menu_id, brand, product_name, basis,
       energy_kcal, protein_g, carbohydrate_g, fat_g, sugar_g, sodium_mg, approved
FROM counted
WHERE product_count = 1
  AND num_nonnulls(energy_kcal, protein_g, carbohydrate_g, fat_g, sugar_g, sodium_mg) > 0;

GRANT USAGE ON SCHEMA pickmenu_franchise TO pickmenu_reference_reader;
GRANT SELECT ON pickmenu_franchise.public_store_nutrition TO pickmenu_reference_reader;

COMMIT;
