import { motion } from 'motion/react';

const categories = [
  { name: 'AI', color: 'bg-purple-500', border: 'border-purple-500' },
  { name: 'Automation', color: 'bg-blue-500', border: 'border-blue-500' },
  { name: 'Analytics', color: 'bg-emerald-500', border: 'border-emerald-500' },
  { name: 'Security', color: 'bg-red-500', border: 'border-red-500' },
  { name: 'Integration', color: 'bg-amber-500', border: 'border-amber-500' },
];

export function GraphLegend() {
  return (
    <motion.div
      initial={{ y: 20, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ delay: 0.3 }}
      className="absolute bottom-6 left-6 bg-slate-900/90 backdrop-blur-md rounded-xl p-4 border border-slate-800 shadow-2xl z-10"
    >
      <div className="text-xs text-slate-500 uppercase tracking-wider mb-3 font-medium">Node Categories</div>
      <div className="space-y-2.5">
        {categories.map((cat, idx) => (
          <motion.div
            key={cat.name}
            initial={{ x: -20, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            transition={{ delay: 0.4 + idx * 0.05 }}
            className="flex items-center gap-2.5"
          >
            <div className={`w-3 h-3 rounded-full ${cat.color} border-2 ${cat.border} shadow-lg`} />
            <span className="text-xs text-slate-300 font-medium">{cat.name}</span>
          </motion.div>
        ))}
      </div>
      <div className="mt-4 pt-3 border-t border-slate-800">
        <div className="text-xs text-slate-500 uppercase tracking-wider mb-2.5 font-medium">Edge Strength</div>
        <div className="space-y-2">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-0.5 bg-purple-500 rounded-full animate-pulse" />
            <span className="text-xs text-slate-300">Strong (&gt;85%)</span>
          </div>
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-0.5 bg-slate-500 rounded-full" />
            <span className="text-xs text-slate-300">Normal</span>
          </div>
        </div>
      </div>
    </motion.div>
  );
}
