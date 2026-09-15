export function validatePublicHttpUrl(input: string, errorPrefix = "article"): URL {
  let url: URL;
  try {
    url = new URL(input);
  } catch {
    throw new Error(`${errorPrefix}_invalid_url`);
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error(`${errorPrefix}_invalid_protocol`);
  }
  if (url.username || url.password) {
    throw new Error(`${errorPrefix}_url_credentials_not_allowed`);
  }

  const hostname = url.hostname.toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");
  if (!hostname || hostname === "localhost" || hostname.endsWith(".localhost") || hostname.endsWith(".local")) {
    throw new Error(`${errorPrefix}_private_host_not_allowed`);
  }
  if (isPrivateIpv4(hostname) || isPrivateIpv6(hostname)) {
    throw new Error(`${errorPrefix}_private_host_not_allowed`);
  }
  return url;
}

function isPrivateIpv4(hostname: string): boolean {
  if (!/^\d{1,3}(?:\.\d{1,3}){3}$/.test(hostname)) return false;
  const octets = hostname.split(".").map(Number);
  if (octets.some((octet) => octet > 255)) return true;
  const [first, second] = octets;
  return first === 0 || first === 10 || first === 127
    || (first === 100 && second >= 64 && second <= 127)
    || (first === 169 && second === 254)
    || (first === 172 && second >= 16 && second <= 31)
    || (first === 192 && second === 168)
    || (first === 198 && (second === 18 || second === 19))
    || first >= 224;
}

function isPrivateIpv6(hostname: string): boolean {
  const normalized = hostname.toLowerCase();
  return normalized === "::" || normalized === "::1"
    || normalized.startsWith("fc") || normalized.startsWith("fd")
    || /^fe[89ab]/.test(normalized)
    // WHATWG URL converts dotted IPv4-mapped hosts to hexadecimal notation.
    // Reject the mapped range instead of checking only the original spelling.
    || normalized.startsWith("::ffff:")
    || normalized.startsWith("ff");
}
