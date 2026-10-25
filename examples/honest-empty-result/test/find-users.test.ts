// Runs with Node's built-in test runner and native TypeScript type stripping
// (Node 22.18+ / 24): node --test examples/honest-empty-result/test/
import { test } from "node:test";
import assert from "node:assert/strict";
import { findUsersByOrg, NO_USERS_MESSAGE } from "../src/find-users.ts";
import type { UserRow, UserStore } from "../src/find-users.ts";

function storeWith(rows: UserRow[]): UserStore {
  return { listUsersByOrg: async () => rows };
}

test("empty result returns the fixed honest message and no IDs", async () => {
  const result = await findUsersByOrg(storeWith([]), "org-without-users");
  assert.deepEqual(result, { found: false, users: [], message: NO_USERS_MESSAGE });
});

test("rows are returned unchanged, nothing added", async () => {
  const rows = [{ id: "u_8f3a", email: "ana@acme.dev" }];
  const result = await findUsersByOrg(storeWith(rows), "acme");
  assert.deepEqual(result, { found: true, users: rows });
});
