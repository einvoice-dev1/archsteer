// LEGACY: Express router with raw SQL inline. This is the pattern agents copy.
const express = require('express');
const { pool } = require('../db/client');

const router = express.Router();

router.get('/users/:id', async (req, res) => {
  const result = await pool.query('SELECT * FROM users WHERE id = $1', [req.params.id]);
  res.json(result.rows[0]);
});

module.exports = router;
