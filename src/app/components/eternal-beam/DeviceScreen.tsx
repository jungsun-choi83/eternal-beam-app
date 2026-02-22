import { ArrowRight, Smartphone, Plus, Battery, HardDrive, Wifi, WifiOff, Settings, Trash2, Download } from 'lucide-react';
import { useLanguage } from '../../contexts/LanguageContext';
import { useState } from 'react';

interface DeviceScreenProps {
  onNavigate?: (screen: string) => void;
}

const mockDevices = [
  {
    id: 1,
    name: 'Eternal Beam #EB12345',
    model: 'EB-2026',
    status: 'online' as const,
    battery: 87,
    storage: 45,
    firmware: 'v2.1.0',
    lastSync: '5분 전'
  },
  {
    id: 2,
    name: 'Eternal Beam #EB67890',
    model: 'EB-2025',
    status: 'offline' as const,
    battery: 23,
    storage: 78,
    firmware: 'v2.0.5',
    lastSync: '2일 전'
  }
];

export function DeviceScreen({ onNavigate }: DeviceScreenProps) {
  const { t } = useLanguage();
  const [devices, setDevices] = useState(mockDevices);

  return (
    <div className="h-full w-full relative overflow-y-auto bg-transparent">
      {/* Header */}
      <div className="glass sticky top-0 z-10 px-6 py-4 border-0 rounded-b-2xl">
        <div className="flex items-center justify-between mb-2">
          <h1 className="font-headline text-2xl font-bold" style={{ color: '#2d3748' }}>
            {t('device.title')}
          </h1>
          <button
            onClick={() => onNavigate?.('home')}
            className="glass-strong w-10 h-10 rounded-full flex items-center justify-center shrink-0"
          >
            <ArrowRight className="w-5 h-5 text-[#7C6B9B]" />
          </button>
        </div>
        <p className="text-sm" style={{ color: '#718096' }}>
          {t('device.connected')}
        </p>
      </div>

      {/* Device List */}
      <div className="px-6 py-6 space-y-4">
        {devices.map((device) => (
          <div
            key={device.id}
            className="glass rounded-2xl overflow-hidden"
          >
            {/* Device Header */}
            <div className="p-4 flex items-center gap-4" style={{
              background: device.status === 'online' 
                ? 'linear-gradient(135deg, rgba(102, 126, 234, 0.1) 0%, rgba(0, 212, 255, 0.1) 100%)'
                : 'rgba(160, 174, 192, 0.1)'
            }}>
              <div
                className="w-14 h-14 rounded-2xl flex items-center justify-center"
                style={{
                  background: device.status === 'online'
                    ? 'linear-gradient(135deg, #667eea 0%, #00d4ff 100%)'
                    : 'linear-gradient(135deg, #a0aec0 0%, #718096 100%)'
                }}
              >
                <Smartphone className="w-7 h-7 text-white" />
              </div>

              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <h3 className="font-bold" style={{ color: '#2d3748' }}>
                    {device.name}
                  </h3>
                  {device.status === 'online' ? (
                    <div className="flex items-center gap-1 px-2 py-1 rounded-full" style={{
                      background: 'rgba(0, 212, 255, 0.2)'
                    }}>
                      <Wifi className="w-3 h-3" style={{ color: '#00d4ff' }} />
                      <span className="text-xs font-semibold" style={{ color: '#00d4ff' }}>
                        {t('device.status.online')}
                      </span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-1 px-2 py-1 rounded-full" style={{
                      background: 'rgba(160, 174, 192, 0.2)'
                    }}>
                      <WifiOff className="w-3 h-3" style={{ color: '#a0aec0' }} />
                      <span className="text-xs font-semibold" style={{ color: '#a0aec0' }}>
                        {t('device.status.offline')}
                      </span>
                    </div>
                  )}
                </div>
                <p className="text-xs" style={{ color: '#718096' }}>
                  {device.model} • {device.firmware}
                </p>
              </div>
            </div>

            {/* Device Stats */}
            <div className="p-4 grid grid-cols-2 gap-4">
              {/* Battery */}
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <Battery className="w-4 h-4" style={{ color: '#667eea' }} />
                  <span className="text-xs font-semibold" style={{ color: '#718096' }}>
                    {t('device.battery')}
                  </span>
                </div>
                <div className="h-2 rounded-full overflow-hidden" style={{
                  background: 'rgba(102, 126, 234, 0.1)'
                }}>
                  <div
                    className="h-full rounded-full transition-all"
                    style={{
                      width: `${device.battery}%`,
                      background: device.battery > 20
                        ? 'linear-gradient(90deg, #667eea 0%, #00d4ff 100%)'
                        : 'linear-gradient(90deg, #ef4444 0%, #dc2626 100%)'
                    }}
                  />
                </div>
                <p className="text-xs mt-1 font-bold" style={{ color: '#2d3748' }}>
                  {device.battery}%
                </p>
              </div>

              {/* Storage */}
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <HardDrive className="w-4 h-4" style={{ color: '#667eea' }} />
                  <span className="text-xs font-semibold" style={{ color: '#718096' }}>
                    {t('device.storage')}
                  </span>
                </div>
                <div className="h-2 rounded-full overflow-hidden" style={{
                  background: 'rgba(102, 126, 234, 0.1)'
                }}>
                  <div
                    className="h-full rounded-full transition-all"
                    style={{
                      width: `${device.storage}%`,
                      background: 'linear-gradient(90deg, #764ba2 0%, #667eea 100%)'
                    }}
                  />
                </div>
                <p className="text-xs mt-1 font-bold" style={{ color: '#2d3748' }}>
                  {device.storage}%
                </p>
              </div>
            </div>

            {/* Last Sync */}
            <div className="px-4 pb-3">
              <p className="text-xs" style={{ color: '#a0aec0' }}>
                {t('device.sync')}: {device.lastSync}
              </p>
            </div>

            {/* Action Buttons */}
            <div className="p-4 pt-0 flex gap-2">
              <button
                className="flex-1 py-2 px-4 rounded-xl flex items-center justify-center gap-2 text-sm font-semibold transition-all"
                style={{
                  background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                  color: 'white',
                  boxShadow: '0 4px 12px rgba(102, 126, 234, 0.3)'
                }}
              >
                <Download className="w-4 h-4" />
                {t('device.firmware')}
              </button>

              <button
                className="w-10 h-10 rounded-xl flex items-center justify-center"
                style={{
                  background: 'rgba(102, 126, 234, 0.1)',
                  color: '#667eea'
                }}
              >
                <Settings className="w-4 h-4" />
              </button>

              <button
                className="w-10 h-10 rounded-xl flex items-center justify-center"
                style={{
                  background: 'rgba(239, 68, 68, 0.1)',
                  color: '#ef4444'
                }}
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* Add Device Button */}
      <div className="px-6 pb-24">
        <button
          onClick={() => onNavigate?.('qrConnection')}
          className="w-full py-4 rounded-2xl flex items-center justify-center gap-2 transition-all"
          style={{
            background: 'white',
            boxShadow: '0 4px 16px rgba(0, 0, 0, 0.08)',
            border: '2px dashed rgba(102, 126, 234, 0.3)',
            color: '#667eea'
          }}
        >
          <Plus className="w-5 h-5" />
          <span className="font-bold">{t('device.add')}</span>
        </button>
      </div>
    </div>
  );
}
