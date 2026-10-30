export interface SessionPublic {
  id: string;
  label: string | null;
  phone: string | null;
  username: string | null;
  first_name: string | null;
  source_bot: string | null;
  created_at: string;
}

export interface SignInResponse {
  password_required: boolean;
  login_id: string | null;
  session: SessionPublic | null;
}
