import { strict as assert } from "node:assert";
import { describe, it } from "node:test";
import { deviceIdFromQr } from "./device-qr.ts";

describe("device QR", () => {
  it("reads a bare device code", () => {
    assert.equal(deviceIdFromQr(" beam-014 "), "beam-014");
  });

  it("reads device_id from a link", () => {
    assert.equal(
      deviceIdFromQr("https://eternalbeam.example/pair?device_id=beam-014"),
      "beam-014",
    );
  });

  it("rejects an empty scan", () => {
    assert.equal(deviceIdFromQr("   "), null);
  });
});
