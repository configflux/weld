import { Router } from "express";

const router = Router();

router.get("/api/items", (_req, res) => {
  res.json([]);
});

router.post("/api/items", (req, res) => {
  res.status(201).json(req.body);
});

export default router;
