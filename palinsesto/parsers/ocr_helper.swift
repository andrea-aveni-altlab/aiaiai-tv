// OCR helper per le griglie Rai (raster dentro i PDF): macOS Vision framework.
// Uso:  swift ocr_helper.swift <immagine.png> [<rects.json>]
//   senza rects: OCR dell'intera immagine -> righe con bounding box
//   con rects (JSON [[x0,y0,x1,y1],...] in pixel, origine in alto a sinistra):
//   OCR per singola regione -> {"regioni": [{"i": n, "righe": [...]}]}
// Output su stdout: JSON. Le coordinate di output sono in pixel immagine,
// origine in alto a sinistra (come pdfplumber/PIL).
import Foundation
import Vision
import AppKit

func ocr(_ cg: CGImage, roi: CGRect?) throws -> [[String: Any]] {
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = ["it-IT"]
    req.usesLanguageCorrection = false
    if let r = roi { req.regionOfInterest = r }
    let handler = VNImageRequestHandler(cgImage: cg)
    try handler.perform([req])
    let W = CGFloat(cg.width), H = CGFloat(cg.height)
    var out: [[String: Any]] = []
    for obs in req.results ?? [] {
        guard let cand = obs.topCandidates(1).first else { continue }
        var bb = obs.boundingBox            // normalizzato, origine in basso a sx
        if let r = roi {                    // riporta al riferimento immagine intera
            bb = CGRect(x: r.minX + bb.minX * r.width, y: r.minY + bb.minY * r.height,
                        width: bb.width * r.width, height: bb.height * r.height)
        }
        out.append(["text": cand.string, "conf": cand.confidence,
                    "x0": bb.minX * W, "x1": bb.maxX * W,
                    "top": (1 - bb.maxY) * H, "bottom": (1 - bb.minY) * H])
    }
    return out
}

let args = CommandLine.arguments
guard args.count >= 2, let img = NSImage(contentsOfFile: args[1]),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("uso: ocr_helper <img.png> [<rects.json>]\n".data(using: .utf8)!)
    exit(1)
}
let W = CGFloat(cg.width), H = CGFloat(cg.height)
var result: Any
if args.count >= 3 {
    let data = try Data(contentsOf: URL(fileURLWithPath: args[2]))
    let rects = try JSONSerialization.jsonObject(with: data) as! [[Double]]
    var regioni: [[String: Any]] = []
    for (i, r) in rects.enumerated() {
        // rects in pixel top-left -> ROI normalizzato bottom-left
        let roi = CGRect(x: r[0] / W, y: 1 - r[3] / H,
                         width: (r[2] - r[0]) / W, height: (r[3] - r[1]) / H)
        regioni.append(["i": i, "righe": try ocr(cg, roi: roi)])
    }
    result = ["regioni": regioni]
} else {
    result = try ocr(cg, roi: nil)
}
let data = try JSONSerialization.data(withJSONObject: result)
print(String(data: data, encoding: .utf8)!)
