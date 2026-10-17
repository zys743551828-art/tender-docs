-- 新空库初始化所需的pgvector纯函数能力；业务仍由受限Worker执行。
-- pgvector安装在design schema，基础角色脚本会撤销PUBLIC的函数执行权。
-- 不授予表owner、其他业务函数或任意schema的泛EXECUTE。
BEGIN;
GRANT EXECUTE ON FUNCTION
 design.vector(design.vector,integer,boolean),
 design.vector_norm(design.vector),
 design.cosine_distance(design.vector,design.vector)
TO tender_worker;
COMMIT;
