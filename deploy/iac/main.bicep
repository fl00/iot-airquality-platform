// ==============================================================================
// Azure Bicep Infrastructure-as-Code Template
// Provisions an ultra-low-cost, bare-metal-optimized Standard_B1s_v2 Linux VM
// ==============================================================================

@description('Azure Region where resources will be deployed')
param location string = resourceGroup().location

@description('Name prefix for all provisioned cloud resources')
param resourcePrefix string = 'baremetal-iot'

@description('Virtual Machine SKU: 1 vCPU, 1 GiB RAM (~$3.80/month)')
param vmSize string = 'Standard_B1s_v2'

@description('Admin username for the Linux Virtual Machine')
param adminUsername string = 'azureuser'

@description('SSH Public Key string for secure authentication')
@secure()
param adminPublicKey string

// 1. Public IP Address
resource publicIp 'Microsoft.Network/publicIPAddresses@2023-09-01' = {
  name: '${resourcePrefix}-pip'
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
    dnsSettings: {
      domainNameLabel: '${resourcePrefix}-${uniqueString(resourceGroup().id)}'
    }
  }
}

// 2. Network Security Group (NSG) with Hardened Ingress Rules
resource nsg 'Microsoft.Network/networkSecurityGroups@2023-09-01' = {
  name: '${resourcePrefix}-nsg'
  location: location
  properties: {
    securityRules: [
      {
        name: 'Allow-SSH-22'
        properties: {
          priority: 1000
          protocol: 'Tcp'
          access: 'Allow'
          direction: 'Inbound'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '22'
        }
      }
      {
        name: 'Allow-HTTP-80'
        properties: {
          priority: 1010
          protocol: 'Tcp'
          access: 'Allow'
          direction: 'Inbound'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '80'
        }
      }
      {
        name: 'Allow-HTTPS-443'
        properties: {
          priority: 1020
          protocol: 'Tcp'
          access: 'Allow'
          direction: 'Inbound'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '443'
        }
      }
      {
        name: 'Allow-MQTT-1883'
        properties: {
          priority: 1030
          protocol: 'Tcp'
          access: 'Allow'
          direction: 'Inbound'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '1883'
        }
      }
    ]
  }
}

// 3. Virtual Network & Subnet
resource vnet 'Microsoft.Network/virtualNetworks@2023-09-01' = {
  name: '${resourcePrefix}-vnet'
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.0.0.0/16'
      ]
    }
    subnets: [
      {
        name: 'default'
        properties: {
          addressPrefix: '10.0.1.0/24'
          networkSecurityGroup: {
            id: nsg.id
          }
        }
      }
    ]
  }
}

// 4. Network Interface Card (NIC)
resource nic 'Microsoft.Network/networkInterfaces@2023-09-01' = {
  name: '${resourcePrefix}-nic'
  location: location
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          subnet: {
            id: vnet.properties.subnets[0].id
          }
          privateIPAllocationMethod: 'Dynamic'
          publicIPAddress: {
            id: publicIp.id
          }
        }
      }
    ]
  }
}

// 5. Virtual Machine (Standard_B1s_v2 Ubuntu 24.04 LTS)
resource vm 'Microsoft.Compute/virtualMachines@2023-09-01' = {
  name: '${resourcePrefix}-vm'
  location: location
  properties: {
    hardwareProfile: {
      vmSize: vmSize
    }
    osProfile: {
      computerName: '${resourcePrefix}-node'
      adminUsername: adminUsername
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: {
          publicKeys: [
            {
              path: '/home/${adminUsername}/.ssh/authorized_keys'
              keyData: adminPublicKey
            }
          ]
        }
      }
    }
    storageProfile: {
      imageReference: {
        publisher: 'Canonical'
        offer: 'ubuntu-24_04-lts'
        sku: 'server'
        version: 'latest'
      }
      osDisk: {
        createOption: 'FromImage'
        managedDisk: {
          storageAccountType: 'StandardSSD_LRS'
        }
        diskSizeGB: 30
      }
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: nic.id
        }
      ]
    }
  }
}

output fqdn string = publicIp.properties.dnsSettings.fqdn
output publicIpAddress string = publicIp.properties.ipAddress
output sshConnectionString string = 'ssh ${adminUsername}@${publicIp.properties.ipAddress}'
