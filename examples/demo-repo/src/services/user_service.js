// TARGET-CONFORMANT: a service that goes through the repository, no raw SQL.
const { findUserById } = require('../repositories/user_repository');

async function getUserProfile(id) {
  const user = await findUserById(id);
  return { id: user.id, name: user.name };
}

module.exports = { getUserProfile };
