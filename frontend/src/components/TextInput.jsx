import { useRef, useState } from 'react'
import { FileText, Type, Upload, X } from 'lucide-react'
import { uploadFile } from '../api.js'
import { useDialog } from './DialogProvider.jsx'

export default function TextInput({ onTextReady }) {
  const { notify } = useDialog()
  const [tab, setTab] = useState('paste')
  const [text, setText] = useState('')
  const [outputName, setOutputName] = useState('')
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [filename, setFilename] = useState('')
  const fileRef = useRef()

  async function handleFile(file) {
    if (!file) return
    if (!file.name.endsWith('.txt')) {
      notify({
        tone: 'warning',
        title: 'File không hợp lệ',
        message: 'Chỉ hỗ trợ file .txt.',
      })
      return
    }
    setUploading(true)
    try {
      const result = await uploadFile(file)
      setText(result.text)
      setFilename(result.filename)
      if (!outputName) setOutputName(result.filename.replace('.txt', ''))
      setTab('paste')
    } catch (e) {
      notify({
        tone: 'error',
        title: 'Không đọc được file',
        message: e.message,
      })
    } finally {
      setUploading(false)
    }
  }

  function onDrop(e) {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files[0]
    handleFile(file)
  }

  function handleSubmit() {
    if (!text.trim()) return
    onTextReady(text.trim(), outputName.trim() || undefined)
  }

  const charCount = text.length
  const wordCount = text.trim() ? text.trim().split(/\s+/).length : 0

  return (
    <div className="glass rounded-2xl overflow-hidden">
      <div className="flex border-b border-surface-600">
        <button
          onClick={() => setTab('paste')}
          className={`flex items-center gap-2 px-5 py-3.5 text-sm font-medium transition-colors ${
            tab === 'paste'
              ? 'text-accent-400 border-b-2 border-accent-500 bg-surface-700'
              : 'text-slate-500 hover:text-slate-300'
          }`}
        >
          <Type size={14} />
          Dán văn bản
        </button>
        <button
          onClick={() => setTab('upload')}
          className={`flex items-center gap-2 px-5 py-3.5 text-sm font-medium transition-colors ${
            tab === 'upload'
              ? 'text-accent-400 border-b-2 border-accent-500 bg-surface-700'
              : 'text-slate-500 hover:text-slate-300'
          }`}
        >
          <Upload size={14} />
          Upload file .txt
        </button>
      </div>

      <div className="p-5 space-y-4">
        {tab === 'paste' ? (
          <>
            {filename && (
              <div className="flex items-center gap-2 text-xs text-success bg-success/10 border border-success/20 rounded-lg px-3 py-2">
                <FileText size={12} />
                Đã tải: <span className="font-medium">{filename}</span>
                <button onClick={() => { setText(''); setFilename('') }} className="ml-auto text-slate-500 hover:text-danger">
                  <X size={12} />
                </button>
              </div>
            )}
            <div className="relative">
              <textarea
                value={text}
                onChange={e => setText(e.target.value)}
                placeholder="Dán nội dung văn bản vào đây..."
                rows={12}
                className="w-full bg-surface-900 border border-surface-500 rounded-xl px-4 py-3 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-accent-500 transition-colors resize-none leading-relaxed"
              />
              {text && (
                <button
                  onClick={() => { setText(''); setFilename('') }}
                  className="absolute top-3 right-3 text-slate-600 hover:text-danger transition-colors"
                >
                  <X size={14} />
                </button>
              )}
            </div>
            <div className="flex justify-between text-xs text-slate-600">
              <span>{charCount.toLocaleString()} ký tự · {wordCount.toLocaleString()} từ</span>
              {charCount > 2500 && (
                <span className="text-accent-400">Sẽ chia thành ~{Math.ceil(charCount / 2500)} đoạn</span>
              )}
            </div>
          </>
        ) : (
          <div
            onDragOver={e => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            onClick={() => fileRef.current?.click()}
            className={`border-2 border-dashed rounded-xl p-12 flex flex-col items-center gap-4 cursor-pointer transition-all ${
              dragging
                ? 'border-accent-500 bg-accent-500/10'
                : 'border-surface-500 hover:border-accent-500/50 hover:bg-surface-700'
            }`}
          >
            <input
              ref={fileRef}
              type="file"
              accept=".txt"
              className="hidden"
              onChange={e => handleFile(e.target.files[0])}
            />
            <div className={`w-16 h-16 rounded-2xl flex items-center justify-center transition-colors ${
              dragging ? 'bg-accent-500/20' : 'bg-surface-600'
            }`}>
              {uploading ? (
                <div className="w-6 h-6 border-2 border-accent-500 border-t-transparent rounded-full animate-spin" />
              ) : (
                <Upload size={28} className={dragging ? 'text-accent-400' : 'text-slate-500'} />
              )}
            </div>
            <div className="text-center">
              <p className="text-sm font-medium text-white">
                {uploading ? 'Đang đọc file...' : 'Kéo thả file .txt vào đây'}
              </p>
              <p className="text-xs text-slate-500 mt-1">hoặc click để chọn file</p>
            </div>
          </div>
        )}

        <div className="flex gap-3">
          <div className="flex-1 space-y-1">
            <label className="text-xs text-slate-500">Tên file output (tùy chọn)</label>
            <input
              type="text"
              value={outputName}
              onChange={e => setOutputName(e.target.value)}
              placeholder="vd: chuong_1 -> chuong_1_FULL.mp3"
              className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-accent-500 transition-colors"
            />
          </div>
        </div>

        <button
          onClick={handleSubmit}
          disabled={!text.trim()}
          className="w-full py-3.5 rounded-xl text-sm font-bold tracking-wide transition-all disabled:opacity-30 disabled:cursor-not-allowed bg-gradient-to-r from-accent-600 to-accent-500 hover:from-accent-500 hover:to-accent-400 text-white shadow-lg shadow-accent-500/20 hover:shadow-accent-500/30"
        >
          Tạo Audio
        </button>
      </div>
    </div>
  )
}
