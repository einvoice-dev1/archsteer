// TARGET: raw SQL lives here, in the repository layer (allowed by ADR 0001).
const { pool } = require('../db/client');

async function findUserById(id) {
  const result = await pool.query('SELECT * FROM users WHERE id = $1', [id]);
  return result.rows[0];
}

module.exports = { findUserById };
