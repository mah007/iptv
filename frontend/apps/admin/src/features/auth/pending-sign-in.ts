/**
 * A sign-in between the password and the authenticator steps. The otpauth
 * URI of a first-time enrolment holds the TOTP secret, so it stays in memory
 * only: never in the URL, history state or browser storage. A reload loses
 * it, and the admin signs in with the password again to get a new one.
 */
export interface PendingSignIn {
  /** Remember (or forget, with null) the enrolment URI of the sign-in in progress. */
  setEnrolment: (otpauthUri: string | null) => void;
  /** The enrolment URI when this sign-in must set up an authenticator first. */
  enrolment: () => string | null;
  /** The second factors the server offers this admin (`totp`, `webauthn`; ADR-0021). */
  setMethods: (methods: readonly string[]) => void;
  methods: () => readonly string[];
}

export function createPendingSignIn(): PendingSignIn {
  let otpauthUri: string | null = null;
  let factors: readonly string[] = ["totp"];
  return {
    setEnrolment: (uri) => {
      otpauthUri = uri;
    },
    enrolment: () => otpauthUri,
    setMethods: (methods) => {
      factors = methods.length > 0 ? methods : ["totp"];
    },
    methods: () => factors,
  };
}
