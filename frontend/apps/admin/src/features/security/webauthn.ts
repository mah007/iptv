/**
 * Passkeys and security keys in the browser (ADR-0021), through @simplewebauthn/browser
 * (MIT). The server sends WebAuthn options as JSON and verifies what comes back.
 */
import {
  browserSupportsWebAuthn,
  startAuthentication,
  startRegistration,
  WebAuthnError,
  type PublicKeyCredentialCreationOptionsJSON,
  type PublicKeyCredentialRequestOptionsJSON,
} from "@simplewebauthn/browser";

/** A browser response, as the API expects it. */
export type KeyResponse = Record<string, unknown>;

export function keysSupported(): boolean {
  return browserSupportsWebAuthn();
}

/** Sign the server's challenge with one of the admin's keys (sign-in or step-up). */
export async function signWithKey(options: Record<string, unknown>): Promise<KeyResponse> {
  const response = await startAuthentication({
    optionsJSON: options as unknown as PublicKeyCredentialRequestOptionsJSON,
  });
  return response as unknown as KeyResponse;
}

/** Create a new credential on a security key or as a passkey. */
export async function createKey(options: Record<string, unknown>): Promise<KeyResponse> {
  const response = await startRegistration({
    optionsJSON: options as unknown as PublicKeyCredentialCreationOptionsJSON,
  });
  return response as unknown as KeyResponse;
}

/** The person closed the browser's prompt, or it timed out: not an error to report. */
export function keyCancelled(error: unknown): boolean {
  if (error instanceof WebAuthnError) {
    return error.code === "ERROR_CEREMONY_ABORTED" || error.name === "NotAllowedError";
  }
  return error instanceof DOMException && error.name === "NotAllowedError";
}
