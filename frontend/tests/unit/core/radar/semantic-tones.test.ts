import { expect, it } from "@rstest/core";

import {
  changeTone,
  historicalSignalTone,
  collectionTone,
} from "@/core/radar/semantic-tones";

it("distinguishes historical signals, missing evidence and unknown labels", () => {
  for (const label of ["历史末段突增", "历史增速上扬", "历史持续爬升"])
    expect(historicalSignalTone(label)).toBe("success");
  expect(historicalSignalTone("历史高位平稳")).toBe("info");
  expect(historicalSignalTone("未命中增强信号")).toBe("neutral");
  expect(historicalSignalTone("历史曲线全零")).toBe("neutral");
  expect(historicalSignalTone("证据不足")).toBe("warning");
  expect(historicalSignalTone("曲线待核验")).toBe("warning");
  expect(historicalSignalTone("future label")).toBe("neutral");
  expect(historicalSignalTone("toString")).toBe("neutral");
});

it("colors measured changes without turning unknown or zero into decline", () => {
  expect(changeTone(9.2)).toBe("success");
  expect(changeTone(-1)).toBe("danger");
  expect(changeTone(0)).toBe("neutral");
  expect(changeTone(null)).toBe("neutral");
});

it("reserves failure red for explicit collection failures", () => {
  expect(collectionTone("data", "ok_zero")).toBe("neutral");
  expect(collectionTone("data", "ok")).toBe("info");
  expect(collectionTone("not_fetched", "timeout")).toBe("danger");
  expect(collectionTone("not_fetched", "forbidden")).toBe("danger");
  expect(collectionTone("not_fetched", "not_reached")).toBe("warning");
  expect(collectionTone("no_data", "no_data")).toBe("warning");
  expect(collectionTone("pending", null)).toBe("info");
  expect(collectionTone("not_fetched", "future_status")).toBe("neutral");
});
