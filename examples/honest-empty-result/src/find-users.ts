// Honest empty results: when the database returns nothing, say so with a fixed
// message. Never invent IDs, names, or rows to make the answer look complete.
//
// Stack-agnostic: `UserStore` is any data source. On Cloudflare D1 it wraps
//   env.DB.prepare("SELECT id, email FROM users WHERE org_id = ?").bind(orgId).all()

export interface UserRow {
  id: string;
  email: string;
}

export interface UserStore {
  listUsersByOrg(orgId: string): Promise<UserRow[]>;
}

export type FindUsersResult =
  | { found: true; users: UserRow[] }
  | { found: false; users: []; message: string };

export const NO_USERS_MESSAGE = "No users found for this organization.";

export async function findUsersByOrg(store: UserStore, orgId: string): Promise<FindUsersResult> {
  const rows = await store.listUsersByOrg(orgId);
  if (rows.length === 0) {
    return { found: false, users: [], message: NO_USERS_MESSAGE };
  }
  return { found: true, users: rows };
}
