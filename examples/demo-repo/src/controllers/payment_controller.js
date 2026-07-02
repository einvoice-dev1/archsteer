// LEGACY: controller importing the DB driver and running raw SQL directly.
const { pool } = require('../db/client');

async function chargePayment(req, res) {
  await pool.query('INSERT INTO payments (user_id, amount) VALUES ($1, $2)', [
    req.body.userId,
    req.body.amount,
  ]);
  res.json({ ok: true });
}

module.exports = { chargePayment };
